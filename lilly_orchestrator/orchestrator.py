"""
Orchestrator - Main entry point for the Lilly multi-agent system.

This is the brain. When you talk to Lilly:
1. She receives your message
2. Router classifies complexity + category
3. Agent pool selects the best agent
4. Fallback chain activates if agent is unavailable
5. Group chat reports the delegation and result
6. Quota tracker records usage

Usage:
    from lilly_orchestrator import Orchestrator
    orch = Orchestrator()
    result = orch.delegate("Fix the bug in lilly_ai.py line 1234")
    print(orch.chat.render_compact())
"""

from __future__ import annotations
import time
import uuid
import json
from typing import Optional, Callable, Any
from pathlib import Path
from dataclasses import dataclass, field

from .agent_pool import AgentPool, Agent, AgentTier
from .quota_tracker import QuotaTracker
from .router import TaskRouter, Task, TaskComplexity, TaskCategory, RoutingDecision
from .group_chat import GroupChat


@dataclass
class OrchestratorState:
    """Persistent state across sessions (Glean: structured state > raw transcripts)."""

    total_tasks: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    tasks_by_agent: dict[str, int] = field(default_factory=dict)
    tasks_by_complexity: dict[str, int] = field(default_factory=dict)
    tasks_by_category: dict[str, int] = field(default_factory=dict)
    session_start: float = 0.0

    def __post_init__(self):
        if self.session_start == 0.0:
            self.session_start = time.time()


class Orchestrator:
    """
    The Lilly Orchestrator — Admin of the agent hive.

    Coordinates 15 agents (1 Admin + 9 Avatars + 6 Coders)
    with seamless delegation and fallback.
    """

    def __init__(self, state_file: Optional[str] = None):
        self.pool = AgentPool()
        self.quota = QuotaTracker(state_file=state_file)
        self.router = TaskRouter(self.pool, self.quota)
        self.chat = GroupChat()
        self.state = OrchestratorState()
        self._tasks: dict[str, Task] = {}
        self._callbacks: dict[str, Callable] = {}

        # Announce startup
        self.chat.lilly_says(
            "Orchestrator online. 15 agents registered. "
            f"Coders: {len(self.pool.get_by_tier(AgentTier.CODER))} | "
            f"Avatars: {len(self.pool.get_by_tier(AgentTier.AVATAR))} | "
            "Ready to delegate."
        )

    def delegate(
        self,
        description: str,
        task_id: Optional[str] = None,
        callback: Optional[Callable] = None,
    ) -> dict:
        """
        Main entry point: receive a task, route it, delegate it.

        Returns a dict with:
          - task_id: unique identifier
          - agent: who's handling it
          - complexity: classified complexity
          - category: classified category
          - reasoning: why this agent was chosen
          - fallback_chain: who takes over if needed
          - context_budget: max tokens to pass
          - chat: the group chat messages so far
        """
        tid = task_id or f"task-{uuid.uuid4().hex[:8]}"
        task = Task(id=tid, description=description)

        # ── Route ──────────────────────────────────────────────────
        decision = self.router.route(task)
        agent = decision.agent

        # ── Record delegation in chat ──────────────────────────────
        self.chat.report_delegation(
            "lilly",
            agent.id,
            description,
            tid,
        )

        # ── Update state ───────────────────────────────────────────
        self.state.total_tasks += 1
        self.state.tasks_by_agent[agent.id] = (
            self.state.tasks_by_agent.get(agent.id, 0) + 1
        )
        self.state.tasks_by_complexity[decision.complexity.name] = (
            self.state.tasks_by_complexity.get(decision.complexity.name, 0) + 1
        )
        self.state.tasks_by_category[decision.category.value] = (
            self.state.tasks_by_category.get(decision.category.value, 0) + 1
        )

        # ── Mark agent busy ────────────────────────────────────────
        self.pool.mark_busy(agent.id)
        task.assigned_agent_id = agent.id
        task.status = "delegated"
        task.started_at = time.time()

        self._tasks[tid] = task
        if callback:
            self._callbacks[tid] = callback

        # ── Check if we should announce fallback availability ──────
        fallback_chain = decision.fallback_chain
        if len(fallback_chain) > 1:
            fallback_names = [
                fa.name
                for fid in fallback_chain[1:]
                if (fa := self.pool.get(fid)) is not None
            ]
            if fallback_names:
                self.chat.agent_says(
                    agent.id,
                    f"Got it. Fallback chain: {' → '.join(fallback_names)} "
                    f"if I can't finish.",
                    msg_type="status",
                    task_id=tid,
                )

        return {
            "task_id": tid,
            "agent": {
                "id": agent.id,
                "name": agent.name,
                "emoji": agent.emoji,
                "tier": agent.tier.value,
                "backend": agent.backend,
            },
            "complexity": decision.complexity.name,
            "category": decision.category.value,
            "reasoning": decision.reasoning,
            "fallback_chain": fallback_chain,
            "context_budget": decision.context_budget,
            "chat": self.chat.render_compact(last_n=5),
        }

    def complete(
        self, task_id: str, result: str, tokens_used: int = 0, latency_ms: float = 0.0
    ):
        """Mark a task as completed and record metrics."""
        task = self._tasks.get(task_id)
        if not task:
            return

        task.status = "completed"
        task.result = result
        task.completed_at = time.time()
        task.tokens_used = tokens_used

        agent_id = task.assigned_agent_id or "lilly"
        self.pool.mark_free(agent_id, tokens_used)
        self.quota.record_usage(agent_id, tokens_used, 0, latency_ms)
        self.quota.record_success(agent_id)

        # Update global state
        self.state.total_tokens += tokens_used
        if tokens_used > 0:
            cost = self.quota.get(agent_id).total_cost
            self.state.total_cost = cost

        # Chat report
        self.chat.report_completion(agent_id, task_id, result[:200])

        # Callback
        cb = self._callbacks.pop(task_id, None)
        if cb:
            cb(task)

    def fail(self, task_id: str, error: str):
        """Mark a task as failed and trigger fallback."""
        task = self._tasks.get(task_id)
        if not task:
            return

        task.status = "failed"
        task.result = error

        agent_id = task.assigned_agent_id or "lilly"
        self.pool.mark_free(agent_id)
        self.quota.record_failure(agent_id, error)

        # Chat report
        self.chat.agent_says(
            agent_id,
            f"❌ Task `{task_id[:12]}` failed: {error[:120]}",
            msg_type="alert",
            task_id=task_id,
        )

        # Trigger fallback
        chain = self.pool.get_fallback_chain(agent_id)
        if len(chain) > 1:
            fallback = chain[1]
            self.chat.report_fallback(agent_id, fallback.id, error, task_id)
            # Re-delegate with fallback
            self.delegate(
                task.description,
                task_id=f"{task_id}-fallback",
            )

    def handle_rate_limit(self, agent_id: str, retry_after: float = 60.0):
        """Handle a rate limit response from an agent."""
        self.quota.record_rate_limit(agent_id, retry_after)
        self.pool.mark_unavailable(agent_id)

        quota = self.quota.get(agent_id)
        self.chat.report_quota_alert(
            agent_id,
            "RATE_LIMITED",
            f"Cooldown {retry_after:.0f}s. "
            f"Hourly: {quota.hourly.token_usage_pct:.0%}. "
            f"Daily: {quota.daily.token_usage_pct:.0%}.",
        )

    def status(self) -> dict:
        """Full orchestrator status."""
        return {
            "state": {
                "total_tasks": self.state.total_tasks,
                "total_tokens": self.state.total_tokens,
                "total_cost": f"${self.state.total_cost:.4f}",
                "uptime": f"{(time.time() - self.state.session_start) / 60:.1f}min",
                "by_agent": self.state.tasks_by_agent,
                "by_complexity": self.state.tasks_by_complexity,
                "by_category": self.state.tasks_by_category,
            },
            "pool": self.pool.status_report(),
            "quotas": self.quota.get_status_summary(),
            "chat": self.chat.render_compact(last_n=15),
        }

    def render_chat(self, last_n: Optional[int] = None) -> str:
        """Render the full group chat."""
        return self.chat.render(last_n=last_n)

    def save_state(self):
        """Persist state to disk (Glean: structured state, not raw transcripts)."""
        state_path = Path("/tmp/lilly_orchestrator_state.json")
        state_path.write_text(
            json.dumps(
                {
                    "total_tasks": self.state.total_tasks,
                    "total_tokens": self.state.total_tokens,
                    "total_cost": self.state.total_cost,
                    "tasks_by_agent": self.state.tasks_by_agent,
                    "tasks_by_complexity": self.state.tasks_by_complexity,
                    "tasks_by_category": self.state.tasks_by_category,
                },
                indent=2,
            )
        )
