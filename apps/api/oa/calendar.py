"""Company calendar and immutable work-segment snapshots for leave accounting."""
from datetime import datetime, time, timedelta

from .domain import SESSIONS, SHANGHAI, parse_time, require, utc_iso


def segments(db, start_at, end_at):
    start, end = parse_time(start_at).astimezone(SHANGHAI), parse_time(end_at).astimezone(SHANGHAI)
    require(end > start, "结束时间必须晚于开始时间")
    require((end - start).days <= 366, "单次请假不能超过 366 天")
    overrides = {
        r["day"]: bool(r["is_workday"])
        for r in db.all("SELECT day,is_workday FROM work_calendar WHERE day>=? AND day<=?", str(start.date()), str(end.date()))
    }
    day, result = start.date(), []
    while day <= end.date():
        if overrides.get(str(day), day.weekday() < 5):
            for sh, sm, eh, em in SESSIONS:
                low = max(start, datetime.combine(day, time(sh, sm), SHANGHAI))
                high = min(end, datetime.combine(day, time(eh, em), SHANGHAI))
                if high > low:
                    result.append([utc_iso(low.isoformat()), utc_iso(high.isoformat())])
        day += timedelta(days=1)
    return result


def hours(work_segments):
    return round(sum((parse_time(b) - parse_time(a)).total_seconds() for a, b in work_segments) / 3600, 2)


def clip(work_segments, start_at, end_at):
    start, end = parse_time(start_at), parse_time(end_at)
    result = []
    for a, b in work_segments:
        low, high = max(start, parse_time(a)), min(end, parse_time(b))
        if high > low:
            result.append([utc_iso(low.isoformat()), utc_iso(high.isoformat())])
    return result


def subtract(work_segments, start_at, end_at):
    start, end = parse_time(start_at), parse_time(end_at)
    result = []
    for a, b in work_segments:
        low, high = parse_time(a), parse_time(b)
        if high <= start or low >= end:
            result.append([a, b])
        else:
            if low < start:
                result.append([a, utc_iso(start.isoformat())])
            if high > end:
                result.append([utc_iso(end.isoformat()), b])
    return result
