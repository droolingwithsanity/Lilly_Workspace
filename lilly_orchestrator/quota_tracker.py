"""
Quota Tracker - Tracks limits, usage, and cooldowns per coding agent.

Implements the Glean pattern:
- Measure token consumption by workflow stage
- Track the "expensive tail" (runs that cost 5-6x median)
- Map per-workflow cost, not per-call
- Detect rate limits and trigger fallback
"""

from __future__ import annotations
import time
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from enum import Enum


class QuotaStatus(Enum):
    AVAILABLE = "available"  # Within limits, can accept tasks
    WARNING = "warning"  # Approaching limit (80%+ used)
    DEPLETED = "depleted"  # Limit reached, fallback needed
    RATE_LIMITED = "rate_limited"  # Temporarily blocked by provider
    COOLDOWN = "cooldown"  # Self-imposed cooldown after errors
    UNAVAILABLE = "unavailable"  # Agent marked unavailable manually


@dataclass
class QuotaWindow:
    """Tracks usage within a time window (hourly, daily, monthly)."""

    window_type: str  # "hourly", "daily", "monthly"
    max_tokens: int  # -1 = unlimited
    max_requests: int  # -1 = unlimited
    current_tokens: int = 0
    current_requests: int = 0
    window_start: float = 0.0
    window_duration: float = 3600.0  # default 1 hour

    def is_expired(self) -> bool:
        if self.window_start == 0:
            return False
        return time.time() - self.window_start > self.window_duration

    def reset(self):
        self.current_tokens = 0
        self.current_requests = 0
        self.window_start = time.time()

    @property
    def token_usage_pct(self) -> float:
        if self.max_tokens <= 0:
            return 0.0
        return min(1.0, self.current_tokens / self.max_tokens)

    @property
    def request_usage_pct(self) -> float:
        if self.max_requests <= 0:
            return 0.0
        return min(1.0, self.current_requests / self.max_requests)


@dataclass
class AgentQuota:
    """Full quota state for one agent."""

    agent_id: str
    status: QuotaStatus = QuotaStatus.AVAILABLE

    # Token limits per window
    hourly: QuotaWindow = field(
        default_factory=lambda: QuotaWindow(
            "hourly", max_tokens=500_000, max_requests=100, window_duration=3600
        )
    )
    daily: QuotaWindow = field(
        default_factory=lambda: QuotaWindow(
            "daily", max_tokens=5_000_000, max_requests=1000, window_duration=86400
        )
    )

    # Rate limit tracking
    rate_limit_until: float = 0.0
    rate_limit_count: int = 0

    # Error tracking
    consecutive_errors: int = 0
    last_error: str = ""
    cooldown_until: float = 0.0

    # Cost tracking
    cost_per_1k_input: float = 0.0  # $/1k input tokens
    cost_per_1k_output: float = 0.0  # $/1k output tokens
    total_cost: float = 0.0

    # Performance metrics
    total_tasks: int = 0
    successful_tasks: int = 0
    failed_tasks: int = 0
    avg_tokens_per_task: int = 0
    avg_latency_ms: float = 0.0

    def record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float = 0.0,
        cost: float = 0.0,
    ):
        total = input_tokens + output_tokens

        # Update windows
        for window in [self.hourly, self.daily]:
            if window.is_expired():
                window.reset()
            window.current_tokens += total
            window.current_requests += 1

        # Update cost
        self.total_cost += cost

        # Update averages (running average)
        self.total_tasks += 1
        if self.total_tasks == 1:
            self.avg_tokens_per_task = total
            self.avg_latency_ms = latency_ms
        else:
            self.avg_tokens_per_task = (
                self.avg_tokens_per_task * (self.total_tasks - 1) + total
            ) // self.total_tasks
            self.avg_latency_ms = (
                self.avg_latency_ms * (self.total_tasks - 1) + latency_ms
            ) / self.total_tasks

        # Check status
        self._update_status()

    def record_success(self):
        self.successful_tasks += 1
        self.consecutive_errors = 0

    def record_failure(self, error: str = ""):
        self.failed_tasks += 1
        self.consecutive_errors += 1
        self.last_error = error

        # Exponential cooldown after consecutive errors
        if self.consecutive_errors >= 3:
            cooldown_seconds = min(300, 30 * (2 ** (self.consecutive_errors - 3)))
            self.cooldown_until = time.time() + cooldown_seconds
            self.status = QuotaStatus.COOLDOWN

    def record_rate_limit(self, retry_after: float = 60.0):
        self.rate_limit_count += 1
        self.rate_limit_until = time.time() + retry_after
        self.status = QuotaStatus.RATE_LIMITED

    @property
    def is_available(self) -> bool:
        return self.status in (QuotaStatus.AVAILABLE, QuotaStatus.WARNING)

    def _update_status(self):
        now = time.time()

        # Check cooldown
        if self.cooldown_until > now:
            self.status = QuotaStatus.COOLDOWN
            return

        # Check rate limit
        if self.rate_limit_until > now:
            self.status = QuotaStatus.RATE_LIMITED
            return

        # Check quota windows
        hourly_pct = max(self.hourly.token_usage_pct, self.hourly.request_usage_pct)
        daily_pct = max(self.daily.token_usage_pct, self.daily.request_usage_pct)
        max_pct = max(hourly_pct, daily_pct)

        if max_pct >= 1.0:
            self.status = QuotaStatus.DEPLETED
        elif max_pct >= 0.8:
            self.status = QuotaStatus.WARNING
        else:
            self.status = QuotaStatus.AVAILABLE

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "status": self.status.value,
            "hourly_tokens": f"{self.hourly.current_tokens:,}/{self.hourly.max_tokens:,}",
            "hourly_pct": f"{self.hourly.token_usage_pct:.1%}",
            "daily_tokens": f"{self.daily.current_tokens:,}/{self.daily.max_tokens:,}",
            "daily_pct": f"{self.daily.token_usage_pct:.1%}",
            "rate_limit_count": self.rate_limit_count,
            "consecutive_errors": self.consecutive_errors,
            "total_cost": f"${self.total_cost:.4f}",
            "success_rate": (
                f"{self.successful_tasks / self.total_tasks:.0%}"
                if self.total_tasks > 0
                else "N/A"
            ),
            "avg_tokens": f"{self.avg_tokens_per_task:,}",
            "avg_latency": f"{self.avg_latency_ms:.0f}ms",
        }


class QuotaTracker:
    """Manages quotas for all coding agents."""

    # Default limits per agent (tokens per window)
    DEFAULT_LIMITS = {
        "opencode": {
            "hourly_tokens": 2_000_000,
            "hourly_requests": 50,
            "daily_tokens": 20_000_000,
            "daily_requests": 500,
            "cost_input": 0.003,
            "cost_output": 0.015,  # Claude Sonnet 4
        },
        "kilo": {
            "hourly_tokens": 2_000_000,
            "hourly_requests": 50,
            "daily_tokens": 20_000_000,
            "daily_requests": 500,
            "cost_input": 0.003,
            "cost_output": 0.015,
        },
        "aider": {
            "hourly_tokens": -1,
            "hourly_requests": -1,  # Unlimited (local)
            "daily_tokens": -1,
            "daily_requests": -1,
            "cost_input": 0.0,
            "cost_output": 0.0,  # Ollama = free
        },
        "codex": {
            "hourly_tokens": 500_000,
            "hourly_requests": 20,
            "daily_tokens": 5_000_000,
            "daily_requests": 200,
            "cost_input": 0.0025,
            "cost_output": 0.01,  # GPT-5.6
        },
        "kiro": {
            "hourly_tokens": 1_000_000,
            "hourly_requests": 30,
            "daily_tokens": 10_000_000,
            "daily_requests": 300,
            "cost_input": 0.003,
            "cost_output": 0.015,
        },
        "openclaw": {
            "hourly_tokens": -1,
            "hourly_requests": -1,  # Unlimited (local)
            "daily_tokens": -1,
            "daily_requests": -1,
            "cost_input": 0.0,
            "cost_output": 0.0,  # Ollama = free
        },
    }

    def __init__(self, state_file: Optional[str] = None):
        self.quotas: dict[str, AgentQuota] = {}
        self.state_file = state_file or "/tmp/lilly_orchestrator_quota.json"
        self._load_state()
        self._init_defaults()

    def _init_defaults(self):
        for agent_id, limits in self.DEFAULT_LIMITS.items():
            if agent_id not in self.quotas:
                quota = AgentQuota(agent_id=agent_id)
                quota.hourly.max_tokens = limits["hourly_tokens"]
                quota.hourly.max_requests = limits["hourly_requests"]
                quota.daily.max_tokens = limits["daily_tokens"]
                quota.daily.max_requests = limits["daily_requests"]
                quota.cost_per_1k_input = limits["cost_input"]
                quota.cost_per_1k_output = limits["cost_output"]
                self.quotas[agent_id] = quota

    def get(self, agent_id: str) -> AgentQuota:
        if agent_id not in self.quotas:
            self.quotas[agent_id] = AgentQuota(agent_id=agent_id)
        return self.quotas[agent_id]

    def is_available(self, agent_id: str) -> bool:
        quota = self.get(agent_id)
        quota._update_status()
        return quota.is_available

    def record_usage(
        self,
        agent_id: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float = 0.0,
    ):
        quota = self.get(agent_id)
        cost = (input_tokens / 1000) * quota.cost_per_1k_input + (
            output_tokens / 1000
        ) * quota.cost_per_1k_output
        quota.record_usage(input_tokens, output_tokens, latency_ms, cost)
        self._save_state()

    def record_success(self, agent_id: str):
        self.get(agent_id).record_success()
        self._save_state()

    def record_failure(self, agent_id: str, error: str = ""):
        self.get(agent_id).record_failure(error)
        self._save_state()

    def record_rate_limit(self, agent_id: str, retry_after: float = 60.0):
        self.get(agent_id).record_rate_limit(retry_after)
        self._save_state()

    def get_status_summary(self) -> dict:
        """Summary of all agent quotas for group chat."""
        summary = {}
        for agent_id, quota in self.quotas.items():
            summary[agent_id] = quota.to_dict()
        return summary

    def get_cheapest_available(self, agent_ids: list[str]) -> Optional[str]:
        """Find the cheapest available agent from a list."""
        best_id = None
        best_cost = float("inf")
        for agent_id in agent_ids:
            if self.is_available(agent_id):
                cost = self.get(agent_id).total_cost
                if cost < best_cost:
                    best_cost = cost
                    best_id = agent_id
        return best_id

    # Free agents: cost = 0 (local Ollama). Always try these first.
    FREE_AGENT_IDS = {"aider", "openclaw"}

    def get_free_first(self, agent_ids: list[str]) -> Optional[str]:
        """Find an available agent, prioritizing FREE agents (Aider/OpenClaw) first.

        Order: free agents (by load) → paid agents (by cost).
        This saves money by using local Ollama before hitting paid APIs.
        """
        # 1. Try free agents first (sorted by load — least busy first)
        free_agents = [
            a for a in agent_ids if a in self.FREE_AGENT_IDS and self.is_available(a)
        ]
        if free_agents:
            # Pick the free agent with lowest load
            free_agents.sort(
                key=lambda a: self.get(a).total_cost
            )  # cost is 0, but sort for determinism
            return free_agents[0]
        # 2. Fall back to paid agents (cheapest first)
        return self.get_cheapest_available(agent_ids)

    def _save_state(self):
        try:
            state = {}
            for agent_id, quota in self.quotas.items():
                state[agent_id] = {
                    "hourly_tokens": quota.hourly.current_tokens,
                    "hourly_requests": quota.hourly.current_requests,
                    "hourly_start": quota.hourly.window_start,
                    "daily_tokens": quota.daily.current_tokens,
                    "daily_requests": quota.daily.current_requests,
                    "daily_start": quota.daily.window_start,
                    "rate_limit_until": quota.rate_limit_until,
                    "rate_limit_count": quota.rate_limit_count,
                    "consecutive_errors": quota.consecutive_errors,
                    "total_cost": quota.total_cost,
                    "total_tasks": quota.total_tasks,
                    "successful_tasks": quota.successful_tasks,
                    "failed_tasks": quota.failed_tasks,
                }
            Path(self.state_file).write_text(json.dumps(state, indent=2))
        except Exception:
            pass  # Don't crash on save failure

    def _load_state(self):
        try:
            path = Path(self.state_file)
            if path.exists():
                state = json.loads(path.read_text())
                for agent_id, data in state.items():
                    quota = self.get(agent_id)
                    quota.hourly.current_tokens = data.get("hourly_tokens", 0)
                    quota.hourly.current_requests = data.get("hourly_requests", 0)
                    quota.hourly.window_start = data.get("hourly_start", 0)
                    quota.daily.current_tokens = data.get("daily_tokens", 0)
                    quota.daily.current_requests = data.get("daily_requests", 0)
                    quota.daily.window_start = data.get("daily_start", 0)
                    quota.rate_limit_until = data.get("rate_limit_until", 0)
                    quota.rate_limit_count = data.get("rate_limit_count", 0)
                    quota.consecutive_errors = data.get("consecutive_errors", 0)
                    quota.total_cost = data.get("total_cost", 0)
                    quota.total_tasks = data.get("total_tasks", 0)
                    quota.successful_tasks = data.get("successful_tasks", 0)
                    quota.failed_tasks = data.get("failed_tasks", 0)
        except Exception:
            pass  # Start fresh on load failure
