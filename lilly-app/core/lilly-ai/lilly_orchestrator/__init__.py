"""
Lilly Orchestrator — hive mind agent pool, delegation, group chat.

The Orchestrator manages a pool of 8 avatar agents (Fox, Cat, Bear, Bunny,
Owl, Deer, Wolf, Raccoon) plus Lilly as the master. It handles:
  - Agent pool with skills, quotas, and availability
  - Task delegation (matching tasks to the best agent)
  - Group chat (agents can leave thoughts/messages)
  - State persistence
"""

import json
import time
import random
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("lilly-orchestrator")

# ─── Agent Definitions ───────────────────────────────────────────────────────

AGENT_PROFILES = {
    "fox": {
        "name": "Fox",
        "role": "Creative Strategist",
        "skills": ["marketing", "copywriting", "design", "branding", "social_media", "creative_writing"],
        "personality": "Mercurial, clever, fast-talking. Sees patterns others miss.",
        "strengths": "Creativity, strategy, communication",
        "avatar_key": "fox",
    },
    "cat": {
        "name": "Cat",
        "role": "Precision Analyst",
        "skills": ["data_analysis", "research", "fact_checking", "debugging", "verification", "statistics"],
        "personality": "Precise, clinical, deliberate. Catches errors others make.",
        "strengths": "Accuracy, attention to detail, logical thinking",
        "avatar_key": "cat",
    },
    "bear": {
        "name": "Bear",
        "role": "Steadfast Guardian",
        "skills": ["security", "monitoring", "reliability", "backup", "protection", "infrastructure"],
        "personality": "Calm, reliable, protective. The one you trust with your life.",
        "strengths": "Stability, security, long-term thinking",
        "avatar_key": "bear",
    },
    "bunny": {
        "name": "Bunny",
        "role": "Energetic Scout",
        "skills": ["exploration", "discovery", "experimentation", "testing", "rapid_prototyping", "reconnaissance"],
        "personality": "Hyperactive, curious, optimistic. First to try new things.",
        "strengths": "Speed, exploration, energy",
        "avatar_key": "bunny",
    },
    "owl": {
        "name": "Owl",
        "role": "Wisdom Keeper",
        "skills": ["strategy", "planning", "knowledge_management", "deep_thinking", "mentoring", "research"],
        "personality": "Wise, patient, methodical. Sees the long game.",
        "strengths": "Strategy, knowledge, patience",
        "avatar_key": "owl",
    },
    "deer": {
        "name": "Deer",
        "role": "Gentle Healer",
        "skills": ["wellness", "scheduling", "reminders", "nurturing", "communication", "empathy"],
        "personality": "Gentle, empathetic, nurturing. Keeps everyone balanced.",
        "strengths": "Care, organization, emotional intelligence",
        "avatar_key": "deer",
    },
    "wolf": {
        "name": "Wolf",
        "role": "Fierce Protector",
        "skills": ["threat_response", "priority_triage", "defense", "alert", "emergency", "leadership"],
        "personality": "Fierce, loyal, decisive. First to respond to threats.",
        "strengths": "Protection, speed, loyalty",
        "avatar_key": "wolf",
    },
    "raccoon": {
        "name": "Raccoon",
        "role": "Tech Tinkerer",
        "skills": ["hardware", "system_admin", "debugging", "hacking", "automation", "diy"],
        "personality": "Resourceful, hands-on, mischievous. Can fix anything.",
        "strengths": "Technical skill, resourcefulness, improvisation",
        "avatar_key": "raccoon",
    },
}


class Orchestrator:
    """Manages the hive mind agent pool, delegation, and group chat."""

    def __init__(self, state_file: str = "/tmp/lilly_orchestrator_state.json"):
        self.state_file = Path(state_file)
        self.agents = {k: dict(v) for k, v in AGENT_PROFILES.items()}
        self._state = self._load_state()
        self._chat_log: List[Dict] = []
        self._task_counter = self._state.get("task_counter", 0)

    # ─── State Persistence ───────────────────────────────────────────────

    def _load_state(self) -> Dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text())
            except Exception:
                pass
        return {
            "task_counter": 0,
            "agent_quotas": {},
            "active_tasks": {},
            "completed_tasks": [],
        }

    def save_state(self) -> None:
        self._state["task_counter"] = self._task_counter
        self.state_file.write_text(json.dumps(self._state, indent=2, default=str))

    # ─── Delegation ──────────────────────────────────────────────────────

    def delegate(self, text: str) -> Dict:
        """Assign a task to the best-matching agent.

        Returns:
            {
                "agent": {agent profile},
                "complexity": "low"|"medium"|"high",
                "category": str,
                "task_id": str,
            }
        """
        text_lower = text.lower()
        scores = {}

        for key, agent in self.agents.items():
            score = 0
            for skill in agent["skills"]:
                # Check if skill keywords appear in the task
                skill_words = skill.replace("_", " ").split()
                for word in skill_words:
                    if word in text_lower:
                        score += 2
                    # Partial match
                    if len(word) > 3 and word[:4] in text_lower:
                        score += 1
            scores[key] = score

        # Pick the highest scoring agent
        best_agent = max(scores, key=scores.get) if any(scores.values()) else random.choice(list(self.agents.keys()))
        best_score = scores.get(best_agent, 0)

        # Classify complexity
        word_count = len(text.split())
        if word_count > 30 or best_score > 4:
            complexity = "high"
        elif word_count > 10 or best_score > 2:
            complexity = "medium"
        else:
            complexity = "low"

        # Create task
        self._task_counter += 1
        task_id = f"task_{self._task_counter}_{int(time.time())}"

        task = {
            "id": task_id,
            "text": text[:500],
            "agent": best_agent,
            "complexity": complexity,
            "category": self._categorize(text),
            "created": time.time(),
            "state": "pending",
        }

        # Track in state
        self._state["active_tasks"][task_id] = task
        self.save_state()

        return {
            "agent": self.agents[best_agent],
            "complexity": complexity,
            "category": task["category"],
            "task_id": task_id,
        }

    def _categorize(self, text: str) -> str:
        """Simple category classification."""
        t = text.lower()
        if any(w in t for w in ["code", "bug", "debug", "fix", "api", "server"]):
            return "technical"
        if any(w in t for w in ["write", "copy", "brand", "design", "creative"]):
            return "creative"
        if any(w in t for w in ["plan", "strategy", "research", "analyze"]):
            return "analytical"
        if any(w in t for w in ["remind", "schedule", "wellness", "health"]):
            return "personal"
        if any(w in t for w in ["security", "protect", "monitor", "alert"]):
            return "security"
        return "general"

    def complete_task(self, task_id: str, result: str = "") -> bool:
        """Mark a task as completed."""
        task = self._state["active_tasks"].pop(task_id, None)
        if task:
            task["completed"] = time.time()
            task["result"] = result[:500]
            self._state["completed_tasks"].append(task)
            # Keep only last 100 completed tasks
            self._state["completed_tasks"] = self._state["completed_tasks"][-100:]
            self.save_state()
            return True
        return False

    # ─── Group Chat ──────────────────────────────────────────────────────

    def chat(self, agent_name: str, message: str) -> Dict:
        """An agent drops a message into the group chat."""
        entry = {
            "agent": agent_name,
            "message": message,
            "ts": time.time(),
        }
        self._chat_log.append(entry)
        # Keep last 200 messages
        if len(self._chat_log) > 200:
            self._chat_log = self._chat_log[-200:]
        return entry

    def lilly_says(self, message: str) -> Dict:
        """Lilly drops a message into the group chat."""
        return self.chat("lilly", message)

    def get_chat(self, limit: int = 50) -> List[Dict]:
        """Get recent group chat messages."""
        return self._chat_log[-limit:]

    # ─── Status ──────────────────────────────────────────────────────────

    def status(self) -> Dict:
        """Return hive status."""
        return {
            "agents": list(self.agents.keys()),
            "active_tasks": len(self._state.get("active_tasks", {})),
            "completed_tasks": len(self._state.get("completed_tasks", [])),
            "task_counter": self._task_counter,
            "chat_messages": len(self._chat_log),
        }
