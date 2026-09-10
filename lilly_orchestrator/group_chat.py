"""
Group Chat - Formats agent reports as a group chat conversation.

The group chat shows:
- 🌟 Lilly (Administrator): orchestrates, delegates, summarizes
- 🦊 Fox, 🐱 Cat, etc. (Co-Admins): status updates, handoff reports
- ⚡ OpenCode, 🔧 Kilo, etc. (Co-Admins): task completion, quota alerts

Each message is a structured report entry with timestamp, agent, and content.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import json


@dataclass
class ChatMessage:
    agent_id: str
    agent_name: str
    agent_emoji: str
    content: str
    timestamp: str = ""
    msg_type: str = "message"  # message, status, alert, delegation, completion
    task_id: Optional[str] = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%H:%M:%S")

    def format(self) -> str:
        """Format as a chat line."""
        prefix = f"{self.agent_emoji} **{self.agent_name}** [{self.timestamp}]"
        return f"{prefix}\n  {self.content}"


class GroupChat:
    """Manages the group chat conversation flow."""

    def __init__(self, max_history: int = 100):
        self.messages: list[ChatMessage] = []
        self.max_history = max_history

    def add(
        self,
        agent_id: str,
        agent_name: str,
        agent_emoji: str,
        content: str,
        msg_type: str = "message",
        task_id: Optional[str] = None,
    ) -> ChatMessage:
        msg = ChatMessage(
            agent_id=agent_id,
            agent_name=agent_name,
            agent_emoji=agent_emoji,
            content=content,
            msg_type=msg_type,
            task_id=task_id,
        )
        self.messages.append(msg)
        if len(self.messages) > self.max_history:
            self.messages = self.messages[-self.max_history :]
        return msg

    def lilly_says(self, content: str, **kwargs) -> ChatMessage:
        return self.add("lilly", "Lilly", "🌟", content, **kwargs)

    def agent_says(self, agent_id: str, content: str, **kwargs) -> ChatMessage:
        from .agent_pool import AgentPool

        # Get agent info from a temporary pool or use defaults
        names = {
            "fox": ("Fox", "🦊"),
            "cat": ("Cat", "🐱"),
            "bear": ("Bear", "🐻"),
            "bunny": ("Bunny", "🐰"),
            "owl": ("Owl", "🦉"),
            "deer": ("Deer", "🦌"),
            "wolf": ("Wolf", "🐺"),
            "raccoon": ("Raccoon", "🦝"),
            "opencode": ("OpenCode", "⚡"),
            "kilo": ("Kilo", "🔧"),
            "aider": ("Aider", "🔗"),
            "codex": ("Codex", "🧠"),
            "kiro": ("Kiro", "📡"),
            "openclaw": ("OpenClaw", "🐾"),
        }
        name, emoji = names.get(agent_id, (agent_id, "🤖"))
        return self.add(agent_id, name, emoji, content, **kwargs)

    def render(self, last_n: Optional[int] = None) -> str:
        """Render the chat as readable text."""
        msgs = self.messages[-last_n:] if last_n else self.messages
        lines = []
        for msg in msgs:
            lines.append(msg.format())
            lines.append("")
        return "\n".join(lines)

    def render_compact(self, last_n: int = 10) -> str:
        """Render last N messages in compact format."""
        msgs = self.messages[-last_n:]
        lines = []
        for msg in msgs:
            lines.append(f"{msg.agent_emoji} {msg.agent_name}: {msg.content[:120]}")
        return "\n".join(lines)

    def to_dict(self) -> list[dict]:
        """Export chat as JSON-serializable list."""
        return [
            {
                "agent_id": m.agent_id,
                "agent_name": m.agent_name,
                "agent_emoji": m.agent_emoji,
                "content": m.content,
                "timestamp": m.timestamp,
                "msg_type": m.msg_type,
                "task_id": m.task_id,
            }
            for m in self.messages
        ]

    # ── Pre-built report templates ──────────────────────────────────

    def report_delegation(
        self, from_agent: str, to_agent: str, task_desc: str, task_id: str
    ):
        """Agent A delegates to Agent B."""
        from_names = {
            "lilly": ("Lilly", "🌟"),
            "fox": ("Fox", "🦊"),
            "cat": ("Cat", "🐱"),
            "raccoon": ("Raccoon", "🦝"),
        }
        to_names = {
            "opencode": ("OpenCode", "⚡"),
            "kilo": ("Kilo", "🔧"),
            "aider": ("Aider", "🔗"),
            "codex": ("Codex", "🧠"),
            "kiro": ("Kiro", "📡"),
            "openclaw": ("OpenClaw", "🐾"),
            "fox": ("Fox", "🦊"),
            "cat": ("Cat", "🐱"),
            "raccoon": ("Raccoon", "🦝"),
            "owl": ("Owl", "🦉"),
        }
        fn, fe = from_names.get(from_agent, (from_agent, "🤖"))
        tn, te = to_names.get(to_agent, (to_agent, "🤖"))
        self.add(
            from_agent,
            fn,
            fe,
            f"Delegating to {te} **{tn}**: _{task_desc[:80]}_",
            msg_type="delegation",
            task_id=task_id,
        )

    def report_status(self, agent_id: str, status: str):
        """Agent reports their status."""
        self.agent_says(agent_id, status, msg_type="status")

    def report_completion(self, agent_id: str, task_id: str, summary: str):
        """Agent reports task completion."""
        self.agent_says(
            agent_id,
            f"✅ Task `{task_id[:12]}` complete: {summary[:120]}",
            msg_type="completion",
            task_id=task_id,
        )

    def report_fallback(
        self, failed_agent: str, fallback_agent: str, reason: str, task_id: str
    ):
        """Fallback triggered: one agent failed, another takes over."""
        self.lilly_says(
            f"⚠️ **Fallback**: {failed_agent} unavailable ({reason}). "
            f"Handing off to **{fallback_agent}**.",
            msg_type="alert",
            task_id=task_id,
        )

    def report_quota_alert(self, agent_id: str, status: str, detail: str):
        """Quota warning or depletion."""
        self.lilly_says(
            f"📊 **Quota Alert** — {agent_id}: {status}. {detail}",
            msg_type="alert",
        )

    def report_pool_status(self, pool_status: dict):
        """Full pool status report."""
        lines = ["📋 **Agent Pool Status**"]
        for agent_id, info in pool_status.items():
            avail = "🟢" if info["available"] else "🔴"
            load = info["load"]
            lines.append(
                f"  {info['emoji']} {info['name']} ({info['tier']}) "
                f"{avail} load={load} "
                f"tasks={info['current_tasks']}/{info['max_concurrent']} "
                f"done={info['total_completed']}"
            )
        self.lilly_says("\n".join(lines), msg_type="status")

    def report_quota_summary(self, quota_summary: dict):
        """Quota summary for all agents."""
        lines = ["💰 **Quota Summary**"]
        for agent_id, info in quota_summary.items():
            status_icon = {
                "available": "🟢",
                "warning": "🟡",
                "depleted": "🔴",
                "rate_limited": "⛔",
                "cooldown": "🧊",
                "unavailable": "⚫",
            }.get(info["status"], "❓")
            lines.append(
                f"  {status_icon} {agent_id}: {info['status']} | "
                f"hourly={info['hourly_pct']} | daily={info['daily_pct']} | "
                f"cost={info['total_cost']} | success={info['success_rate']}"
            )
        self.lilly_says("\n".join(lines), msg_type="status")
