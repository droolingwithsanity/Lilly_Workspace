"""
Task Router - Routes tasks by complexity to the right agent.

Implements the Glean patterns:
- Route by complexity/risk/context-depth before execution
- Minimal tool set per route (agent sees only what it needs)
- Match thinking budget to difficulty
- Context shrinks between steps, not grows

TaskComplexity determines which tier of agent handles the task:
  TRIVIAL  → Avatar agent or cheapest coder (quick factual, one-liner)
  SIMPLE   → Primary coder (standard code edit, single file)
  MODERATE → Avatar specialist or coder (multi-file, needs planning)
  COMPLEX  → Coder with highest thinking budget (architecture, refactoring)
  CRITICAL → Admin (Lilly) oversees, delegates to best available
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import re
import time

from .agent_pool import AgentPool, Agent, AgentTier, AgentRole
from .quota_tracker import QuotaTracker


class TaskComplexity(Enum):
    TRIVIAL = 1  # Quick lookup, one-liner, simple question
    SIMPLE = 2  # Standard code edit, single file, straightforward
    MODERATE = 3  # Multi-file, needs some planning, moderate risk
    COMPLEX = 4  # Architecture, refactoring, high-risk changes
    CRITICAL = 5  # System-wide impact, security, production changes


class TaskCategory(Enum):
    CODE_EDIT = "code_edit"
    CODE_REVIEW = "code_review"
    DEBUGGING = "debugging"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    CREATIVE = "creative"
    ANALYSIS = "analysis"
    PLANNING = "planning"
    MONITORING = "monitoring"
    SECURITY = "security"
    DEVOPS = "devops"
    CONVERSATION = "conversation"
    SENSORS = "sensors"
    PHONE = "phone"
    UNKNOWN = "unknown"


@dataclass
class Task:
    id: str
    description: str
    complexity: TaskComplexity = TaskComplexity.SIMPLE
    category: TaskCategory = TaskCategory.UNKNOWN
    required_capabilities: Optional[list[str]] = None
    assigned_agent_id: Optional[str] = None
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    status: str = "pending"  # pending, delegated, in_progress, completed, failed
    result: str = ""
    fallback_reason: str = ""
    tokens_used: int = 0

    def __post_init__(self):
        if self.required_capabilities is None:
            self.required_capabilities = []
        if self.created_at == 0.0:
            self.created_at = time.time()


# ── Keyword maps for classification ────────────────────────────────
_CODE_KEYWORDS = {
    TaskCategory.CODE_EDIT: [
        "code",
        "write",
        "create",
        "implement",
        "add",
        "modify",
        "change",
        "update",
        "refactor",
        "rename",
        "move",
        "delete",
        "fix",
        "patch",
        "function",
        "class",
        "method",
        "variable",
        "import",
        "export",
        "python",
        "javascript",
        "typescript",
        "java",
        "rust",
        "go",
        "html",
        "css",
        "sql",
        "bash",
        "shell",
        "script",
    ],
    TaskCategory.CODE_REVIEW: [
        "review",
        "check",
        "audit",
        "inspect",
        "analyze code",
        "code review",
        "pr review",
        "pull request",
        "diff",
    ],
    TaskCategory.DEBUGGING: [
        "debug",
        "error",
        "bug",
        "crash",
        "traceback",
        "exception",
        "not working",
        "broken",
        "fails",
        "issue",
        "problem",
        "why does",
        "what's wrong",
        "fix this",
    ],
    TaskCategory.TESTING: [
        "test",
        "spec",
        "assert",
        "mock",
        "pytest",
        "jest",
        "unittest",
        "coverage",
        "integration test",
        "e2e",
        "regression",
    ],
    TaskCategory.DOCUMENTATION: [
        "document",
        "readme",
        "docs",
        "comment",
        "docstring",
        "explain this code",
        "how does",
        "what does this do",
    ],
    TaskCategory.CREATIVE: [
        "write",
        "story",
        "poem",
        "creative",
        "brainstorm",
        "idea",
        "design",
        "name",
        "suggest",
        "imagine",
        "describe",
    ],
    TaskCategory.ANALYSIS: [
        "analyze",
        "compare",
        "evaluate",
        "assess",
        "measure",
        "benchmark",
        "performance",
        "metrics",
        "statistics",
    ],
    TaskCategory.PLANNING: [
        "plan",
        "strategy",
        "roadmap",
        "architecture",
        "design",
        "approach",
        "how should we",
        "what's the best way",
    ],
    TaskCategory.MONITORING: [
        "status",
        "check",
        "monitor",
        "watch",
        "track",
        "health",
        "sensor",
        "battery",
        "wifi",
        "location",
        "notification",
    ],
    TaskCategory.SECURITY: [
        "security",
        "vulnerability",
        "auth",
        "permission",
        "encrypt",
        "token",
        "secret",
        "credential",
        "access control",
    ],
    TaskCategory.DEVOPS: [
        "deploy",
        "docker",
        "compose",
        "ci/cd",
        "pipeline",
        "build",
        "container",
        "kubernetes",
        "nginx",
        "server",
    ],
    TaskCategory.CONVERSATION: [
        "hello",
        "hi",
        "how are you",
        "what do you think",
        "tell me",
        "chat",
        "talk",
        "opinion",
    ],
    TaskCategory.PHONE: [
        "phone",
        "android",
        "termux",
        "ssh",
        "app",
        "overlay",
        "notification",
        "camera",
        "microphone",
        "bluetooth",
    ],
}


@dataclass
class RoutingDecision:
    """The result of routing a task."""

    task: Task
    agent: Agent
    complexity: TaskComplexity
    category: TaskCategory
    reasoning: str
    fallback_chain: list[str]
    context_budget: int  # max tokens to pass as context


class TaskRouter:
    """Routes tasks to the best available agent."""

    def __init__(self, pool: AgentPool, quota: QuotaTracker):
        self.pool = pool
        self.quota = quota

    def classify(
        self, description: str
    ) -> tuple[TaskComplexity, TaskCategory, list[str]]:
        """Classify a task by complexity, category, and required capabilities."""
        text = description.lower()

        # ── Category detection ─────────────────────────────────────
        category = TaskCategory.UNKNOWN
        best_score = 0
        for cat, keywords in _CODE_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score:
                best_score = score
                category = cat

        # ── Complexity detection ───────────────────────────────────
        complexity = TaskComplexity.SIMPLE  # default

        # Complexity signals
        complex_signals = [
            "architecture",
            "refactor",
            "system",
            "pipeline",
            "multiple",
            "across",
            "entire",
            "all files",
            "big picture",
            "strategy",
            "production",
            "critical",
            "security",
            "migration",
        ]
        moderate_signals = [
            "implement",
            "create",
            "add feature",
            "build",
            "integrate",
            "multi-file",
            "several",
            "component",
            "module",
            "api",
        ]
        trivial_signals = [
            "what is",
            "how do",
            "quick",
            "one line",
            "simple",
            "rename",
            "move",
            "copy",
            "what's the",
            "status",
        ]

        if any(sig in text for sig in complex_signals):
            complexity = TaskComplexity.COMPLEX
        elif any(sig in text for sig in moderate_signals):
            complexity = TaskComplexity.MODERATE
        elif any(sig in text for sig in trivial_signals):
            complexity = TaskComplexity.TRIVIAL

        # Critical: security + production together
        if ("security" in text or "production" in text) and (
            "fix" in text or "deploy" in text or "critical" in text
        ):
            complexity = TaskComplexity.CRITICAL

        # ── Required capabilities ──────────────────────────────────
        caps = set()
        for cat_keywords in _CODE_KEYWORDS.values():
            for kw in cat_keywords:
                if kw in text:
                    caps.add(kw)

        # Add category-specific base capabilities
        cap_map = {
            TaskCategory.CODE_EDIT: ["coding"],
            TaskCategory.CODE_REVIEW: ["code_review"],
            TaskCategory.DEBUGGING: ["debugging"],
            TaskCategory.TESTING: ["testing"],
            TaskCategory.DOCUMENTATION: ["documentation"],
            TaskCategory.CREATIVE: ["creative_writing", "brainstorming"],
            TaskCategory.ANALYSIS: ["data_analysis", "deep_analysis"],
            TaskCategory.PLANNING: ["planning", "strategy"],
            TaskCategory.MONITORING: ["monitoring", "status_checks"],
            TaskCategory.SECURITY: ["security"],
            TaskCategory.DEVOPS: ["docker", "devops"],
            TaskCategory.CONVERSATION: ["conversation"],
            TaskCategory.PHONE: ["phone", "sensors"],
        }
        if category in cap_map:
            caps.update(cap_map[category])

        return complexity, category, list(caps)

    def route(self, task: Task) -> RoutingDecision:
        """Route a task to the best agent."""
        complexity, category, caps = self.classify(task.description)
        task.complexity = complexity
        task.category = category
        task.required_capabilities = caps

        # ── Select agent based on complexity + category ────────────
        agent = None
        reasoning = ""

        if complexity == TaskComplexity.CRITICAL:
            # Critical: Lilly oversees, delegates to best coder
            coder = self._select_best_coder(caps)
            if coder:
                agent = coder
                reasoning = (
                    f"Critical task routed to {coder.name} "
                    f"(highest available thinking budget). "
                    f"Lilly monitors."
                )
            else:
                agent = self.pool.get("lilly")
                reasoning = (
                    "Critical task, but no coders available. Lilly handles directly."
                )

        elif complexity in (TaskComplexity.COMPLEX, TaskComplexity.MODERATE):
            # Complex/Moderate: try avatar specialist first, then coder
            avatar = self._select_best_avatar(category, caps)
            coder = self._select_best_coder(caps)

            if avatar and coder:
                # Both available: prefer coder for code tasks, avatar for non-code
                if category in (
                    TaskCategory.CODE_EDIT,
                    TaskCategory.DEBUGGING,
                    TaskCategory.TESTING,
                    TaskCategory.DEVOPS,
                ):
                    agent = coder
                    reasoning = (
                        f"Complex code task → {coder.name} "
                        f"(primary coder, thinking_budget={coder.thinking_budget})."
                    )
                else:
                    agent = avatar
                    reasoning = (
                        f"Moderate/complex task → {avatar.name} "
                        f"(specialist in {category.value})."
                    )
            elif coder:
                agent = coder
                reasoning = f"Complex task → {coder.name} (only coder available)."
            elif avatar:
                agent = avatar
                reasoning = f"Complex task → {avatar.name} (only specialist available)."
            else:
                agent = self.pool.get("lilly")
                reasoning = "No agents available. Lilly handles directly."

        elif complexity == TaskComplexity.SIMPLE:
            # Simple: prefer avatar specialist if category matches, else cheapest coder
            avatar = self._select_best_avatar(category, caps)
            # Avatar-preferred categories (non-code work)
            avatar_categories = {
                TaskCategory.CREATIVE,
                TaskCategory.ANALYSIS,
                TaskCategory.PLANNING,
                TaskCategory.MONITORING,
                TaskCategory.SECURITY,
                TaskCategory.CONVERSATION,
                TaskCategory.DOCUMENTATION,
            }
            if avatar and category in avatar_categories:
                agent = avatar
                reasoning = f"Simple task → {avatar.name} (avatar specialist in {category.value})."
            else:
                coder = self._select_best_coder(caps)
                if coder:
                    agent = coder
                    reasoning = (
                        f"Simple task → {coder.name} (cheapest available coder)."
                    )
                elif avatar:
                    agent = avatar
                    reasoning = f"Simple task → {avatar.name} (avatar specialist)."
                else:
                    agent = self.pool.get("lilly")
                    reasoning = "Simple task, no coders available. Lilly handles."

        else:  # TRIVIAL
            # Trivial: route to cheapest available, prefer local/free
            free_agents = ["aider", "openclaw"]
            for fid in free_agents:
                if self.quota.is_available(fid):
                    free_agent = self.pool.get(fid)
                    if free_agent is not None:
                        agent = free_agent
                        reasoning = f"Trivial task → {agent.name} (free local LLM)."
                        break
            if not agent:
                coder = self._select_best_coder(caps)
                if coder:
                    agent = coder
                    reasoning = f"Trivial task → {coder.name} (cheapest available)."
                else:
                    agent = self.pool.get("lilly")
                    reasoning = "Trivial task. Lilly handles."

        # ── Build fallback chain ───────────────────────────────────
        fallback_chain = (
            [a.id for a in self.pool.get_fallback_chain(agent.id)]
            if agent
            else ["lilly"]
        )

        # ── Context budget (Glean: context shrinks, not grows) ────
        context_budget = {
            TaskComplexity.TRIVIAL: 500,
            TaskComplexity.SIMPLE: 2000,
            TaskComplexity.MODERATE: 5000,
            TaskComplexity.COMPLEX: 10000,
            TaskComplexity.CRITICAL: 15000,
        }.get(complexity, 2000)

        resolved_agent = agent if agent is not None else self.pool.get("lilly")
        assert resolved_agent is not None
        task.assigned_agent_id = resolved_agent.id

        return RoutingDecision(
            task=task,
            agent=resolved_agent,
            complexity=complexity,
            category=category,
            reasoning=reasoning,
            fallback_chain=fallback_chain,
            context_budget=context_budget,
        )

    def _select_best_avatar(
        self, category: TaskCategory, caps: list[str]
    ) -> Optional[Agent]:
        """Select the best available avatar for a category."""
        category_to_role = {
            TaskCategory.CREATIVE: AgentRole.CREATIVE,
            TaskCategory.ANALYSIS: AgentRole.ANALYST,
            TaskCategory.PLANNING: AgentRole.WISDOM,
            TaskCategory.MONITORING: AgentRole.SCOUT,
            TaskCategory.SECURITY: AgentRole.PROTECTOR,
            TaskCategory.CONVERSATION: AgentRole.COMPANION,
        }
        preferred_role = category_to_role.get(category)

        avatars = self.pool.get_available(AgentTier.AVATAR)

        # Prefer role match
        if preferred_role:
            role_matches = [a for a in avatars if a.role == preferred_role]
            if role_matches:
                return max(role_matches, key=lambda a: a.priority)

        # Fall back to capability match
        matches = self.pool.find_by_capabilities(caps[:3])
        avatar_matches = [a for a in matches if a.tier == AgentTier.AVATAR]
        if avatar_matches:
            return avatar_matches[0]

        # Last resort: any available avatar
        return avatars[0] if avatars else None

    def _select_best_coder(self, caps: list[str]) -> Optional[Agent]:
        """Select the best available coder, respecting quota."""
        coders = self.pool.get_available(AgentTier.CODER)

        # Score each coder: priority + capability match - cost
        scored = []
        for coder in coders:
            if not self.quota.is_available(coder.id):
                continue

            cap_match = len(set(caps) & set(coder.capabilities))
            quota = self.quota.get(coder.id)

            # Score: higher is better
            score = (
                coder.priority
                + (cap_match * 10)
                + (coder.thinking_budget * 0.5)
                - (quota.total_cost * 100)  # penalize expensive agents
            )
            scored.append((score, coder))

        if scored:
            scored.sort(key=lambda x: -x[0])
            return scored[0][1]

        return None
