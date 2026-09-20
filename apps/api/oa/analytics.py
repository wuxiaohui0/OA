"""Read-only approval analytics built from the immutable approval journal.

The analytics layer deliberately does not keep a second copy of workflow data.  This
keeps the numbers explainable: every duration and risk item can be traced back to a
leave request and its approval actions.
"""

from collections import defaultdict
from datetime import datetime, timezone

from .db import camel_row
from .domain import LEAVE_TYPES, SHANGHAI


PENDING_STATUSES = {
    "submitted",
    "validating",
    "agent_reviewing",
    "human_reviewing",
    "need_information",
}
TERMINAL_STATUSES = {"approved", "rejected"}


def _time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _month(value):
    parsed = _time(value)
    return parsed.astimezone(SHANGHAI).strftime("%Y-%m") if parsed else "未知"


def _duration_hours(start, end):
    if not start or not end:
        return None
    return max(0.0, (end - start).total_seconds() / 3600)


class AnalyticsService:
    def __init__(self, db, organization):
        self.db = db
        self.organization = organization

    def _scope(self, user):
        # Organization managers are the only role that can see cross-employee
        # analytics.  The permission check also covers HR and system admins.
        self.organization.require_access(user)

    def _rows(self):
        requests = self.db.all(
            self.db.LEAVE_SELECT
            + " WHERE lr.deleted_at IS NULL ORDER BY lr.created_at DESC"
        )
        actions = defaultdict(list)
        for action in self.db.all(
            "SELECT request_id, action, reason, created_at FROM approval_actions ORDER BY id"
        ):
            actions[action["request_id"]].append(action)
        result = []
        for raw_request in requests:
            request = camel_row(raw_request)
            request_actions = actions[request["id"]]
            submitted = next(
                (_time(item["created_at"]) for item in request_actions if item["action"] == "submit"),
                _time(request["createdAt"]),
            )
            completed = next(
                (
                    _time(item["created_at"])
                    for item in request_actions
                    if item["action"] in TERMINAL_STATUSES
                    or item["action"] in {"approve", "reject"}
                ),
                None,
            )
            # Approval actions use approve/reject as their action name while
            # the status uses approved/rejected.
            if completed is None:
                for item in request_actions:
                    if item["action"] in ("approve", "reject"):
                        completed = _time(item["created_at"])
                        break
            age_start = submitted or _time(request["updatedAt"])
            result.append(
                {
                    "request": request,
                    "actions": request_actions,
                    "submitted": submitted,
                    "completed": completed,
                    "processingHours": _duration_hours(submitted, completed),
                    "pendingHours": _duration_hours(age_start, datetime.now(timezone.utc)),
                }
            )
        return result

    @staticmethod
    def _bucket_template(key, label=None):
        return {
            "key": key,
            "label": label if label is not None else key,
            "total": 0,
            "pending": 0,
            "approved": 0,
            "rejected": 0,
            "hours": 0,
            "avgProcessingHours": None,
        }

    def report(self, user):
        self._scope(user)
        rows = self._rows()
        now = datetime.now(timezone.utc)
        total = len(rows)
        status_counts = defaultdict(int)
        processing = []
        departments = {}
        types = {key: self._bucket_template(key, value) for key, value in LEAVE_TYPES.items()}
        months = {}
        local_now = now.astimezone(SHANGHAI)
        month_index = local_now.year * 12 + local_now.month - 1
        for offset in range(5, -1, -1):
            index = month_index - offset
            year, month = divmod(index, 12)
            key = f"{year:04d}-{month + 1:02d}"
            months[key] = {"period": key, "submitted": 0, "approved": 0, "rejected": 0}

        for row in rows:
            request = row["request"]
            status = request["status"]
            status_counts[status] += 1
            if row["processingHours"] is not None and status in TERMINAL_STATUSES:
                processing.append(row["processingHours"])
            department = request["department"] or "未分配"
            department_bucket = departments.setdefault(department, self._bucket_template(department))
            type_bucket = types.setdefault(
                request["leaveType"], self._bucket_template(request["leaveType"], LEAVE_TYPES.get(request["leaveType"], request["leaveType"]))
            )
            for bucket in (department_bucket, type_bucket):
                bucket["total"] += 1
                bucket["hours"] += request["durationHours"] or 0
                if status in PENDING_STATUSES:
                    bucket["pending"] += 1
                elif status == "approved":
                    bucket["approved"] += 1
                elif status == "rejected":
                    bucket["rejected"] += 1
                if row["processingHours"] is not None and status in TERMINAL_STATUSES:
                    bucket.setdefault("_processing", []).append(row["processingHours"])
            period = _month(request["createdAt"])
            if period in months:
                months[period]["submitted"] += 1
                if status == "approved":
                    months[period]["approved"] += 1
                elif status == "rejected":
                    months[period]["rejected"] += 1

        def finalize(bucket):
            durations = bucket.pop("_processing", [])
            bucket["hours"] = round(bucket["hours"], 2)
            bucket["avgProcessingHours"] = round(sum(durations) / len(durations), 2) if durations else None
            return bucket

        approved = status_counts["approved"]
        rejected = status_counts["rejected"]
        decided = approved + rejected
        overdue = [
            row
            for row in rows
            if row["request"]["status"] in PENDING_STATUSES and (row["pendingHours"] or 0) >= 48
        ]
        overdue.sort(key=lambda item: item["pendingHours"] or 0, reverse=True)
        missing_approver = [
            row for row in rows if row["request"]["status"] in PENDING_STATUSES and not row["request"]["currentApproverId"]
        ]
        escalated = [
            row for row in rows if row["request"].get("agentDecision") == "escalate"
        ]

        def risk_item(row, risk_type, title):
            request = row["request"]
            return {
                "type": risk_type,
                "title": title,
                "requestId": request["id"],
                "applicantName": request["applicantName"],
                "department": request["department"],
                "status": request["status"],
                "hours": round(row["pendingHours"] or 0, 2),
                "approverName": request["currentApproverName"],
            }

        return {
            "generatedAt": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "scope": "organization",
            "summary": {
                "total": total,
                "pending": sum(status_counts[status] for status in PENDING_STATUSES),
                "approved": approved,
                "rejected": rejected,
                "draft": status_counts["draft"],
                "withdrawn": status_counts["withdrawn"],
                "cancelled": status_counts["cancelled"],
                "totalHours": round(sum(row["request"]["durationHours"] or 0 for row in rows), 2),
                "avgProcessingHours": round(sum(processing) / len(processing), 2) if processing else None,
                "approvalRate": round(approved / decided * 100, 1) if decided else None,
                "overdue": len(overdue),
                "aiReviewed": sum(row["request"].get("agentDecision") is not None for row in rows),
                "aiEscalated": len(escalated),
            },
            "trend": list(months.values()),
            "byDepartment": sorted((finalize(bucket) for bucket in departments.values()), key=lambda item: item["total"], reverse=True),
            "byLeaveType": [finalize(types[key]) for key in LEAVE_TYPES if key in types],
            "risks": {
                "overdue": [risk_item(row, "overdue", "审批超过 48 小时") for row in overdue[:20]],
                "missingApprover": [risk_item(row, "missing_approver", "待办缺少审批人") for row in missing_approver[:20]],
                "aiEscalated": [risk_item(row, "ai_escalated", "智能审核建议升级人工核验") for row in escalated[:20]],
            },
        }

    def export_rows(self, user):
        self._scope(user)
        exported = []
        for row in self._rows():
            request = row["request"]
            exported.append(
                {
                    "requestId": request["id"],
                    "applicantName": request["applicantName"],
                    "department": request["department"],
                    "leaveType": LEAVE_TYPES.get(request["leaveType"], request["leaveType"]),
                    "status": request["status"],
                    "durationHours": request["durationHours"],
                    "createdAt": request["createdAt"],
                    "updatedAt": request["updatedAt"],
                    "processingHours": round(row["processingHours"], 2) if row["processingHours"] is not None else "",
                    "approverName": request["currentApproverName"] or "",
                    "agentDecision": request["agentDecision"] or "",
                    "reason": request["reason"],
                }
            )
        return exported
