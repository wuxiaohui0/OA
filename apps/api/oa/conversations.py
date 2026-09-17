import time
import json
from uuid import uuid4

from .domain import require


class ConversationStore:
    """Server-owned history, isolated by user. Run one API worker while using this store."""

    def __init__(self, now=time.time, ttl=12 * 60 * 60, db=None):
        self.items, self.now, self.ttl = {}, now, ttl
        self.db = db

    def save(self, conversation):
        if self.db:
            self.db.execute("""INSERT INTO assistant_conversations VALUES(?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at""",
                conversation["id"], conversation["userId"],
                json.dumps({**conversation, "busy": False}, ensure_ascii=False), self.now())

    def get(self, user_id, conversation_id):
        conversation = self.items.get(conversation_id)
        if not conversation and self.db:
            row = self.db.one("SELECT payload FROM assistant_conversations WHERE id=? AND user_id=?", conversation_id, user_id)
            if row:
                conversation = json.loads(row["payload"])
                self.items[conversation_id] = conversation
        require(
            conversation
            and conversation["userId"] == user_id
            and (conversation["busy"] or self.now() - conversation["touchedAt"] <= self.ttl),
            "对话不存在或已过期，请开始新对话。",
            404,
        )
        conversation["touchedAt"] = self.now()
        self.save(conversation)
        return conversation

    def begin(self, user_id, conversation_id=None, *, rotate=False):
        for key, value in list(self.items.items()):
            if not value["busy"] and self.now() - value["touchedAt"] > self.ttl:
                del self.items[key]
        if conversation_id:
            conversation = self.get(user_id, conversation_id)
        else:
            require(len(self.items) < 1000, "当前对话较多，请稍后重试。", 503)
            conversation = {
                "id": str(uuid4()),
                "userId": user_id,
                "messages": [],
                "draftId": None,
                "touchedAt": self.now(),
                "busy": False,
                "contextId": None,
            }
            self.items[conversation["id"]] = conversation
            self.save(conversation)
        require(not conversation["busy"], "上一条消息仍在处理中，请稍候。", 409)
        if rotate and len(conversation["messages"]) >= 60:
            conversation["messages"] = conversation["messages"][-40:]
        require(len(conversation["messages"]) < 60, "当前对话已达到长度上限，请开始新对话。", 409)
        conversation["busy"] = True
        return conversation

    def finish(self, conversation):
        conversation.update(busy=False, touchedAt=self.now())
        if not conversation["messages"]:
            self.items.pop(conversation["id"], None)
        else:
            self.save(conversation)

    @staticmethod
    def snapshot(conversation, draft):
        return {
            "id": conversation["id"],
            "messages": conversation["messages"],
            "draft": draft,
            "busy": conversation["busy"],
        }

    @staticmethod
    def sync_draft(conversation, find):
        if not conversation["draftId"]:
            return None
        leave = find(conversation["draftId"])
        if leave and leave["status"] == "draft":
            return leave
        message = (
            "该请假草稿已取消，不会提交审批。"
            if not leave or leave["status"] in ("cancelled", "withdrawn")
            else f"该申请已提交，当前状态：{leave['status']}。不要重复创建或提交这笔申请。"
        )
        conversation["messages"].append({"role": "assistant", "outcome": "completed", "content": message})
        conversation["draftId"] = None
        return None
