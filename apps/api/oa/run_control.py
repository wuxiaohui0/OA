"""In-process control plane for active OA Agent runs.

The current deployment deliberately uses one API worker (see ConversationStore).
For a multi-worker deployment this registry must move to a shared run service
using Redis/pub-sub or LangGraph Agent Server cancellation.
"""

import asyncio
from dataclasses import dataclass, field
from threading import RLock


@dataclass
class ActiveAgentRun:
    user_id: str
    conversation_id: str
    task: asyncio.Task
    stopped: asyncio.Event = field(default_factory=asyncio.Event)


class AgentRunRegistry:
    """Own active model tasks and cancel them at their next await boundary."""

    def __init__(self):
        self._runs: dict[tuple[str, str], ActiveAgentRun] = {}
        self._lock = RLock()

    @staticmethod
    def _key(user_id, conversation_id):
        return str(user_id), str(conversation_id)

    def start(self, user_id, conversation_id, task):
        key = self._key(user_id, conversation_id)
        control = ActiveAgentRun(*key, task)
        with self._lock:
            current = self._runs.get(key)
            if current and not current.stopped.is_set():
                raise RuntimeError("Conversation already has an active Agent run")
            self._runs[key] = control
        return control

    def request_pause(self, user_id, conversation_id):
        with self._lock:
            control = self._runs.get(self._key(user_id, conversation_id))
        if not control or control.task.done():
            return None
        # task.cancel() is cooperative: synchronous domain transactions finish
        # atomically and cancellation is delivered at the next async boundary.
        control.task.get_loop().call_soon_threadsafe(control.task.cancel)
        return control

    def finish(self, control):
        key = self._key(control.user_id, control.conversation_id)
        with self._lock:
            if self._runs.get(key) is control:
                self._runs.pop(key, None)
        control.stopped.set()


AGENT_RUNS = AgentRunRegistry()
