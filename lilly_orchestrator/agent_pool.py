"""
Agent Pool - Registry of all 15 agents with capabilities, tiers, and roles.

Agent Tiers:
  ADMIN     - Lilly (the orchestrator, top-level decision maker)
  AVATAR    - 9 personality-driven specialists (Fox, Cat, Bear, etc.)
  CODER     - 6 CLI coding agents (opencode, kilo, aider, codex, kiro, openclaw)

Each agent declares:
  - role: what they're responsible for
  - capabilities: keyword tags for routing
  - strengths: what they excel at
  - backend: which LLM/service powers them
  - fallback: who takes over if this agent is unavailable
  - max_concurrent: how many tasks simultaneously
  - thinking_budget: complexity cap (Glean pattern)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class AgentTier(Enum):
    ADMIN = "admin"  # Lilly - orchestrator
    AVATAR = "avatar"  # Personality specialists
    CODER = "coder"  # CLI coding agents


class AgentRole(Enum):
    # Avatar roles
    COMPANION = "companion"  # Puppy/Lilly - conversation, memory
    CREATIVE = "creative"  # Fox - writing, brainstorming
    ANALYST = "analyst"  # Cat - data, code review, facts
    GUARDIAN = "guardian"  # Bear - scheduling, practical
    SCOUT = "scout"  # Bunny - monitoring, alerts
    WISDOM = "wisdom"  # Owl - deep analysis, planning
    HEALER = "healer"  # Deer - emotional, wellness
    PROTECTOR = "protector"  # Wolf - security, automation
    TINKERER = "tinkerer"  # Raccoon - coding, gadgets
    # Coder roles
    PRIMARY_CODER = "primary_coder"  # opencode - main coding agent
    SECONDARY_CODER = "secondary_coder"  # kilo, aider - fallback coders
    SPECIALIST_CODER = "specialist_coder"  # codex, kiro, openclaw - niche


@dataclass
class Agent:
    id: str
    name: str
    emoji: str
    tier: AgentTier
    role: AgentRole
    capabilities: list[str]
    strengths: list[str]
    backend: str
    description: str
    max_concurrent: int = 1
    thinking_budget: int = 100  # 0-100, higher = more reasoning allowed
    fallback_id: Optional[str] = None  # who to delegate to if unavailable
    available: bool = True
    current_tasks: int = 0
    last_active: float = 0.0
    total_completed: int = 0
    total_tokens_used: int = 0
    priority: int = 50  # 0-100, higher = preferred

    @property
    def is_available(self) -> bool:
        return self.available and self.current_tasks < self.max_concurrent

    @property
    def load_factor(self) -> float:
        if self.max_concurrent == 0:
            return 1.0
        return self.current_tasks / self.max_concurrent


class AgentPool:
    """Registry and lookup for all agents in the system."""

    def __init__(self):
        self.agents: dict[str, Agent] = {}
        self._register_defaults()

    def _register_defaults(self):
        """Register all 15 agents: 1 Admin + 9 Avatars + 6 Coders."""

        # ── ADMIN ──────────────────────────────────────────────────
        self.register(
            Agent(
                id="lilly",
                name="Lilly",
                emoji="🌟",
                tier=AgentTier.ADMIN,
                role=AgentRole.COMPANION,
                capabilities=[
                    "conversation",
                    "memory",
                    "emotional_intelligence",
                    "orchestration",
                    "delegation",
                    "planning",
                    "summarization",
                    "sensors",
                    "phone",
                    "notifications",
                    "camera",
                    "voice",
                    "everything",
                ],
                strengths=[
                    "conversation",
                    "memory",
                    "delegation",
                    "emotional_intelligence",
                    "planning",
                    "context_management",
                    "multi_task_coordination",
                ],
                backend="anthropic/claude-sonnet-4-6",
                description="Administrator. Orchestrates all agents, manages memory, "
                "delegates tasks, and reports status. The primary interface.",
                max_concurrent=10,
                thinking_budget=95,
                priority=100,
            )
        )

        # ── AVATAR AGENTS (Co-Administrators) ──────────────────────
        self.register(
            Agent(
                id="fox",
                name="Fox",
                emoji="🦊",
                tier=AgentTier.AVATAR,
                role=AgentRole.CREATIVE,
                capabilities=[
                    "creative_writing",
                    "storytelling",
                    "brainstorming",
                    "design",
                    "ui_ux",
                    "naming",
                    "copywriting",
                    "poetry",
                ],
                strengths=["creativity", "speed", "playful_thinking"],
                backend="anthropic/claude-sonnet-4-6",
                description="Creative Strategist. Handles writing, brainstorming, "
                "design concepts, and imaginative tasks.",
                max_concurrent=3,
                thinking_budget=85,
                fallback_id="lilly",
                priority=75,
            )
        )

        self.register(
            Agent(
                id="cat",
                name="Cat",
                emoji="🐱",
                tier=AgentTier.AVATAR,
                role=AgentRole.ANALYST,
                capabilities=[
                    "data_analysis",
                    "code_review",
                    "fact_checking",
                    "debugging",
                    "testing",
                    "optimization",
                    "architecture",
                    "documentation",
                    "refactoring",
                ],
                strengths=["precision", "methodical_thinking", "detail_oriented"],
                backend="anthropic/claude-sonnet-4-6",
                description="Precision Analyst. Handles code review, data analysis, "
                "fact-checking, and technical documentation.",
                max_concurrent=3,
                thinking_budget=90,
                fallback_id="lilly",
                priority=75,
            )
        )

        self.register(
            Agent(
                id="bear",
                name="Bear",
                emoji="🐻",
                tier=AgentTier.AVATAR,
                role=AgentRole.GUARDIAN,
                capabilities=[
                    "scheduling",
                    "reminders",
                    "routines",
                    "practical_advice",
                    "organization",
                    "task_management",
                    "project_planning",
                ],
                strengths=["dependability", "calmness", "practical_wisdom"],
                backend="anthropic/claude-sonnet-4-6",
                description="Steadfast Guardian. Handles scheduling, organization, "
                "reminders, and practical day-to-day tasks.",
                max_concurrent=2,
                thinking_budget=70,
                fallback_id="lilly",
                priority=65,
            )
        )

        self.register(
            Agent(
                id="bunny",
                name="Bunny",
                emoji="🐰",
                tier=AgentTier.AVATAR,
                role=AgentRole.SCOUT,
                capabilities=[
                    "monitoring",
                    "alerts",
                    "real_time_tracking",
                    "notifications",
                    "sensors",
                    "status_checks",
                    "environmental_awareness",
                    "bluetooth",
                    "wifi",
                ],
                strengths=["speed", "vigilance", "enthusiasm"],
                backend="anthropic/claude-sonnet-4-6",
                description="Energetic Scout. Handles real-time monitoring, alerts, "
                "sensor data, and environmental awareness.",
                max_concurrent=5,
                thinking_budget=60,
                fallback_id="lilly",
                priority=70,
            )
        )

        self.register(
            Agent(
                id="owl",
                name="Owl",
                emoji="🦉",
                tier=AgentTier.AVATAR,
                role=AgentRole.WISDOM,
                capabilities=[
                    "deep_analysis",
                    "long_term_planning",
                    "research",
                    "strategy",
                    "architecture_design",
                    "risk_assessment",
                    "philosophy",
                    "complex_reasoning",
                ],
                strengths=["wisdom", "patience", "deep_thinking"],
                backend="anthropic/claude-sonnet-4-6",
                description="Wisdom Keeper. Handles deep analysis, long-term strategy, "
                "research synthesis, and complex reasoning.",
                max_concurrent=1,
                thinking_budget=100,
                fallback_id="lilly",
                priority=60,
            )
        )

        self.register(
            Agent(
                id="deer",
                name="Deer",
                emoji="🦌",
                tier=AgentTier.AVATAR,
                role=AgentRole.HEALER,
                capabilities=[
                    "emotional_support",
                    "wellness",
                    "meditation",
                    "health_tracking",
                    "empathy",
                    "calming",
                    "relationship_advice",
                    "self_care",
                ],
                strengths=["empathy", "warmth", "nurturing"],
                backend="anthropic/claude-sonnet-4-6",
                description="Gentle Healer. Handles emotional support, wellness "
                "tracking, meditation, and relationship guidance.",
                max_concurrent=2,
                thinking_budget=75,
                fallback_id="owl",
                priority=60,
            )
        )

        self.register(
            Agent(
                id="wolf",
                name="Wolf",
                emoji="🐺",
                tier=AgentTier.AVATAR,
                role=AgentRole.PROTECTOR,
                capabilities=[
                    "security",
                    "threat_assessment",
                    "privacy",
                    "automation",
                    "access_control",
                    "monitoring",
                    "incident_response",
                    "compliance",
                ],
                strengths=["decisiveness", "loyalty", "strategic_thinking"],
                backend="anthropic/claude-sonnet-4-6",
                description="Fierce Protector. Handles security analysis, automation "
                "rules, threat assessment, and incident response.",
                max_concurrent=2,
                thinking_budget=85,
                fallback_id="lilly",
                priority=70,
            )
        )

        self.register(
            Agent(
                id="raccoon",
                name="Raccoon",
                emoji="🦝",
                tier=AgentTier.AVATAR,
                role=AgentRole.TINKERER,
                capabilities=[
                    "coding",
                    "hacking",
                    "gadgets",
                    "tools",
                    "cli",
                    "debugging",
                    "scripting",
                    "automation",
                    "devops",
                    "docker",
                    "git",
                    "pip",
                    "npm",
                ],
                strengths=["curiosity", "resourcefulness", "hands_on"],
                backend="anthropic/claude-sonnet-4-6",
                description="Tech Tinkerer. Handles coding tasks, DevOps, "
                "tool management, scripting, and hardware hacking.",
                max_concurrent=3,
                thinking_budget=85,
                fallback_id="cat",
                priority=80,
            )
        )

        # ── CODING AGENTS (Co-Administrators - CLI workers) ────────
        self.register(
            Agent(
                id="opencode",
                name="OpenCode",
                emoji="⚡",
                tier=AgentTier.CODER,
                role=AgentRole.PRIMARY_CODER,
                capabilities=[
                    "coding",
                    "refactoring",
                    "debugging",
                    "testing",
                    "architecture",
                    "docker",
                    "git",
                    "python",
                    "javascript",
                    "typescript",
                    "shell",
                    "devops",
                ],
                strengths=["code_quality", "full_stack", "well_integrated"],
                backend="anthropic/claude-sonnet-4-6",
                description="Primary coding agent. Well-integrated with workspace, "
                "MCP servers, and project references.",
                max_concurrent=2,
                thinking_budget=90,
                fallback_id="kilo",
                priority=95,
            )
        )

        self.register(
            Agent(
                id="kilo",
                name="Kilo",
                emoji="🔧",
                tier=AgentTier.CODER,
                role=AgentRole.SECONDARY_CODER,
                capabilities=[
                    "coding",
                    "refactoring",
                    "debugging",
                    "testing",
                    "bash",
                    "file_operations",
                    "permissions",
                ],
                strengths=["permissive_access", "flexibility", "speed"],
                backend="anthropic/claude-sonnet-4-6",
                description="Secondary coding agent. Broad file access, "
                "flexible permission model.",
                max_concurrent=2,
                thinking_budget=85,
                fallback_id="aider",
                priority=85,
            )
        )

        self.register(
            Agent(
                id="aider",
                name="Aider",
                emoji="🔗",
                tier=AgentTier.CODER,
                role=AgentRole.SECONDARY_CODER,
                capabilities=[
                    "coding",
                    "refactoring",
                    "git_aware",
                    "multi_file_edit",
                    "python",
                    "javascript",
                    "testing",
                ],
                strengths=["git_integration", "multi_file_editing", "local_models"],
                backend="ollama/qwen2.5-coder:14b",
                description="Git-aware coding agent. Excellent at multi-file edits "
                "with local Ollama backend (no API costs).",
                max_concurrent=1,
                thinking_budget=75,
                fallback_id="opencode",
                priority=70,
            )
        )

        self.register(
            Agent(
                id="codex",
                name="Codex",
                emoji="🧠",
                tier=AgentTier.CODER,
                role=AgentRole.SPECIALIST_CODER,
                capabilities=[
                    "coding",
                    "code_review",
                    "reasoning",
                    "architecture",
                    "complex_debugging",
                    "system_design",
                ],
                strengths=["deep_reasoning", "gpt_powered", "code_review"],
                backend="openai/gpt-5.6-terra",
                description="OpenAI Codex CLI. Deep reasoning with GPT models, "
                "strong at complex architectural decisions.",
                max_concurrent=1,
                thinking_budget=100,
                fallback_id="opencode",
                priority=75,
            )
        )

        self.register(
            Agent(
                id="kiro",
                name="Kiro",
                emoji="📡",
                tier=AgentTier.CODER,
                role=AgentRole.SPECIALIST_CODER,
                capabilities=[
                    "coding",
                    "spec_driven",
                    "steering",
                    "documentation",
                    "requirements",
                    "planning",
                ],
                strengths=["spec_compliance", "structured_approach", "planning"],
                backend="anthropic/claude-sonnet-4-6",
                description="AWS Kiro. Spec-driven coding with structured "
                "requirements and steering documents.",
                max_concurrent=1,
                thinking_budget=85,
                fallback_id="opencode",
                priority=60,
            )
        )

        self.register(
            Agent(
                id="openclaw",
                name="OpenClaw",
                emoji="🐾",
                tier=AgentTier.CODER,
                role=AgentRole.SPECIALIST_CODER,
                capabilities=[
                    "coding",
                    "local_llm",
                    "ollama",
                    "multi_model",
                    "agents",
                    "canvas",
                    "devices",
                ],
                strengths=["local_processing", "multi_model", "privacy"],
                backend="ollama/qwen2.5:7b",
                description="Local-first coding agent. Runs on Ollama with "
                "multiple model support. Good for private tasks.",
                max_concurrent=1,
                thinking_budget=70,
                fallback_id="aider",
                priority=55,
            )
        )

    def register(self, agent: Agent):
        self.agents[agent.id] = agent

    def get(self, agent_id: str) -> Optional[Agent]:
        return self.agents.get(agent_id)

    def get_by_tier(self, tier: AgentTier) -> list[Agent]:
        return [a for a in self.agents.values() if a.tier == tier]

    def get_by_role(self, role: AgentRole) -> list[Agent]:
        return [a for a in self.agents.values() if a.role == role]

    def get_available(self, tier: Optional[AgentTier] = None) -> list[Agent]:
        agents = self.agents.values()
        if tier:
            agents = [a for a in agents if a.tier == tier]
        return [a for a in agents if a.is_available]

    def get_fallback_chain(self, agent_id: str) -> list[Agent]:
        """Build the fallback chain starting from the given agent."""
        chain = []
        visited = set()
        current_id = agent_id
        while current_id and current_id not in visited:
            agent = self.agents.get(current_id)
            if agent:
                chain.append(agent)
                visited.add(current_id)
                current_id = agent.fallback_id
        return chain

    def find_by_capabilities(self, required: list[str]) -> list[Agent]:
        """Find agents that have ALL required capabilities, sorted by priority."""
        matches = []
        for agent in self.agents.values():
            if agent.is_available and all(c in agent.capabilities for c in required):
                matches.append(agent)
        return sorted(matches, key=lambda a: (-a.priority, a.load_factor))

    def mark_busy(self, agent_id: str):
        agent = self.agents.get(agent_id)
        if agent:
            agent.current_tasks += 1
            agent.last_active = time.time()

    def mark_free(self, agent_id: str, tokens_used: int = 0):
        agent = self.agents.get(agent_id)
        if agent:
            agent.current_tasks = max(0, agent.current_tasks - 1)
            agent.total_completed += 1
            agent.total_tokens_used += tokens_used

    def mark_unavailable(self, agent_id: str):
        agent = self.agents.get(agent_id)
        if agent:
            agent.available = False

    def mark_available(self, agent_id: str):
        agent = self.agents.get(agent_id)
        if agent:
            agent.available = True

    def status_report(self) -> dict:
        """Full pool status for the group chat."""
        report = {}
        for agent_id, agent in self.agents.items():
            report[agent_id] = {
                "name": agent.name,
                "emoji": agent.emoji,
                "tier": agent.tier.value,
                "role": agent.role.value,
                "available": agent.available,
                "current_tasks": agent.current_tasks,
                "max_concurrent": agent.max_concurrent,
                "load": f"{agent.load_factor:.0%}",
                "total_completed": agent.total_completed,
                "total_tokens": agent.total_tokens_used,
            }
        return report
