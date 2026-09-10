"""
Lilly Orchestrator - Multi-Agent Task Delegation System

Lilly is the Administrator. She delegates to Co-Administrators:
- 9 Avatar Agents (personality-driven specialists)
- 6 Coding Agents (CLI-based workers with fallback chains)

Inspired by Glean's token efficiency patterns:
- Route by complexity, not one-size-fits-all
- Context shrinks between steps, not grows
- Bounded loops with clear exit conditions
- Structured state replaces raw transcripts
"""

__version__ = "0.1.0"

from .agent_pool import AgentPool, Agent, AgentTier, AgentRole
from .quota_tracker import QuotaTracker
from .router import TaskRouter, TaskComplexity
from .orchestrator import Orchestrator
from .group_chat import GroupChat
