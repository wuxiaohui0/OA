import pytest

from oa.conversations import ConversationStore
from oa.domain import BusinessError


def test_locks_isolation_failure_cleanup_and_expiry():
    clock = [0]
    store = ConversationStore(now=lambda: clock[0], ttl=10)
    item = store.begin("alice")
    with pytest.raises(BusinessError, match="处理中"):
        store.begin("alice", item["id"])
    with pytest.raises(BusinessError, match="不存在"):
        store.get("bob", item["id"])
    clock[0] = 100
    assert store.get("alice", item["id"]) == item
    store.finish(item)
    assert item["id"] not in store.items
    item = store.begin("alice")
    item["messages"].append({"role": "user", "content": "测试"})
    store.finish(item)
    clock[0] += 11
    with pytest.raises(BusinessError, match="过期"):
        store.get("alice", item["id"])


def test_message_and_store_limits():
    store = ConversationStore()
    item = store.begin("alice")
    item["messages"] = [{"role": "user", "content": "测试"}] * 60
    store.finish(item)
    with pytest.raises(BusinessError, match="长度上限"):
        store.begin("alice", item["id"])
    assert len(store.get("alice", item["id"])["messages"]) == 60
    for _ in range(999):
        store.begin("bob")
    with pytest.raises(BusinessError, match="对话较多"):
        store.begin("bob")
