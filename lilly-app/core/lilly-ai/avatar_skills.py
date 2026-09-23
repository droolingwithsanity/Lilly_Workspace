"""Avatar Skill System - assigns capabilities to each of the 9 avatars.

Each avatar has specific skills they can offer to users. Users enable/disable
skills per avatar. Skills trigger proactive actions (morning briefings, scheduled
DMs, memory updates, etc.).
"""

import json
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger("AvatarSkills")

DATA_DIR = Path(__file__).parent.parent.parent / "data"
SKILLS_FILE = DATA_DIR / "avatar_skills.json"
USER_SKILLS_FILEPattern = "user_skills_{user_id}.json"


class SkillCategory(str, Enum):
    PROACTIVE = "proactive"      # Triggers actions without user asking
    RESPONSIVE = "responsive"    # Responds to user requests
    SCHEDULED = "scheduled"      # Time-based actions
    MEMORY = "memory"            # Persistent memory features


@dataclass
class Skill:
    id: str
    name: str
    description: str
    category: SkillCategory
    avatars: List[str]           # Which avatars can use this skill
    enabled_by_default: bool = True
    config_schema: Dict = field(default_factory=dict)  # Expected config
    requires_google: bool = False  # Needs Google OAuth tokens
    requires_ig: bool = False     # Needs Instagram access


# ── All available skills ────────────────────────────────────────────────────

SKILL_REGISTRY: Dict[str, Skill] = {
    # ── Proactive Skills ──────────────────────────────────────────────────
    "morning_briefing": Skill(
        id="morning_briefing",
        name="Morning Briefing",
        description="Daily summary of schedule, weather, priorities, and news",
        category=SkillCategory.PROACTIVE,
        avatars=["puppy", "owl", "bunny"],
        enabled_by_default=True,
        config_schema={
            "time": "08:00",           # When to send (HH:MM)
            "timezone": "UTC",
            "include_weather": True,
            "include_calendar": True,
            "include_news": True,
        },
        requires_google=True,
    ),
    "evening_recap": Skill(
        id="evening_recap",
        name="Evening Recap",
        description="End-of-day summary of accomplishments and tomorrow's prep",
        category=SkillCategory.PROACTIVE,
        avatars=["puppy", "owl"],
        enabled_by_default=True,
        config_schema={
            "time": "20:00",
            "timezone": "UTC",
        },
        requires_google=True,
    ),
    "wellness_check": Skill(
        id="wellness_check",
        name="Wellness Check",
        description="Periodic mood tracking, breathing exercises, self-care reminders",
        category=SkillCategory.PROACTIVE,
        avatars=["deer", "puppy"],
        enabled_by_default=True,
        config_schema={
            "frequency": "daily",       # daily, twice_daily, weekly
            "time": "12:00",
            "timezone": "UTC",
        },
    ),
    "security_monitor": Skill(
        id="security_monitor",
        name="Security Monitor",
        description="Monitors for suspicious activity, login alerts, threat warnings",
        category=SkillCategory.PROACTIVE,
        avatars=["wolf"],
        enabled_by_default=True,
        config_schema={
            "check_interval_minutes": 60,
            "alert_threshold": "medium",  # low, medium, high, critical
        },
    ),

    # ── Scheduled Skills ──────────────────────────────────────────────────
    "scheduled_dm": Skill(
        id="scheduled_dm",
        name="Scheduled DMs",
        description="Release DMs at specific times you choose",
        category=SkillCategory.SCHEDULED,
        avatars=["puppy", "fox", "cat", "bear", "bunny", "owl", "deer", "wolf", "raccoon"],
        enabled_by_default=True,
        config_schema={
            "max_per_day": 10,
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "timezone": "UTC",
        },
    ),
    "calendar_manager": Skill(
        id="calendar_manager",
        name="Calendar Manager",
        description="Schedule meetings, resolve conflicts, send reminders",
        category=SkillCategory.SCHEDULED,
        avatars=["bear", "cat"],
        enabled_by_default=True,
        config_schema={
            "auto_resolve_conflicts": True,
            "reminder_before_minutes": 15,
        },
        requires_google=True,
    ),
    "content_scheduler": Skill(
        id="content_scheduler",
        name="Content Scheduler",
        description="Schedule Instagram posts and stories at optimal times",
        category=SkillCategory.SCHEDULED,
        avatars=["fox", "bunny"],
        enabled_by_default=True,
        config_schema={
            "max_posts_per_day": 3,
            "optimal_times": ["09:00", "12:00", "18:00"],
        },
        requires_ig=True,
    ),

    # ── Memory Skills ─────────────────────────────────────────────────────
    "persistent_memory": Skill(
        id="persistent_memory",
        name="Persistent Memory",
        description="Remembers your preferences, projects, and habits across sessions",
        category=SkillCategory.MEMORY,
        avatars=["puppy", "owl", "cat"],
        enabled_by_default=True,
        config_schema={
            "memory_depth": "deep",     # shallow, medium, deep
            "auto_summarize": True,
            "retain_days": 90,
        },
    ),
    "project_tracker": Skill(
        id="project_tracker",
        name="Project Tracker",
        description="Tracks ongoing projects, deadlines, and progress",
        category=SkillCategory.MEMORY,
        avatars=["cat", "bear"],
        enabled_by_default=True,
        config_schema={
            "auto_update": True,
            "deadline_reminders": True,
        },
    ),
    "habit_tracker": Skill(
        id="habit_tracker",
        name="Habit Tracker",
        description="Tracks daily habits, streaks, and patterns",
        category=SkillCategory.MEMORY,
        avatars=["deer", "puppy"],
        enabled_by_default=True,
        config_schema={
            "track_streaks": True,
            "weekly_report": True,
        },
    ),

    # ── Responsive Skills ─────────────────────────────────────────────────
    "creative_assistant": Skill(
        id="creative_assistant",
        name="Creative Assistant",
        description="Brainstorming, writing, ideas, creative projects",
        category=SkillCategory.RESPONSIVE,
        avatars=["fox"],
        enabled_by_default=True,
        config_schema={},
    ),
    "tech_support": Skill(
        id="tech_support",
        name="Tech Support",
        description="Debugging, coding, system help, technical guidance",
        category=SkillCategory.RESPONSIVE,
        avatars=["raccoon"],
        enabled_by_default=True,
        config_schema={},
    ),
    "data_analysis": Skill(
        id="data_analysis",
        name="Data Analysis",
        description="Reports, charts, data insights, research",
        category=SkillCategory.RESPONSIVE,
        avatars=["cat", "owl"],
        enabled_by_default=True,
        config_schema={},
    ),
    "news_monitor": Skill(
        id="news_monitor",
        name="News Monitor",
        description="Real-time news, trending topics, custom alerts",
        category=SkillCategory.RESPONSIVE,
        avatars=["bunny"],
        enabled_by_default=True,
        config_schema={
            "topics": [],               # User-defined topics to track
            "alert_urgency": "medium",
        },
    ),
}


def get_skill(skill_id: str) -> Optional[Skill]:
    """Get a skill by ID."""
    return SKILL_REGISTRY.get(skill_id)


def get_skills_for_avatar(avatar_key: str) -> List[Skill]:
    """Get all skills available to a specific avatar."""
    return [s for s in SKILL_REGISTRY.values() if avatar_key in s.avatars]


def get_all_skills() -> List[Skill]:
    """Get all registered skills."""
    return list(SKILL_REGISTRY.values())


def get_skills_by_category(category: SkillCategory) -> List[Skill]:
    """Get all skills in a category."""
    return [s for s in SKILL_REGISTRY.values() if s.category == category]


# ── User Skill Preferences ──────────────────────────────────────────────────

def _user_skills_path(user_id: str) -> Path:
    """Path to user's skill preferences file."""
    user_dir = DATA_DIR / "users" / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "skills.json"


def load_user_skills(user_id: str) -> Dict[str, Dict]:
    """Load user's skill preferences.

    Returns dict of skill_id -> {enabled: bool, config: dict}
    """
    path = _user_skills_path(user_id)
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception as e:
        logger.warning(f"Failed to load skills for {user_id}: {e}")

    # Return defaults for all skills
    defaults = {}
    for skill_id, skill in SKILL_REGISTRY.items():
        defaults[skill_id] = {
            "enabled": skill.enabled_by_default,
            "config": skill.config_schema.copy(),
        }
    return defaults


def save_user_skills(user_id: str, skills: Dict[str, Dict]) -> None:
    """Save user's skill preferences."""
    path = _user_skills_path(user_id)
    try:
        path.write_text(json.dumps(skills, indent=2))
    except Exception as e:
        logger.error(f"Failed to save skills for {user_id}: {e}")


def enable_skill(user_id: str, skill_id: str, config: Optional[Dict] = None) -> bool:
    """Enable a skill for a user."""
    if skill_id not in SKILL_REGISTRY:
        return False

    skills = load_user_skills(user_id)
    skill = SKILL_REGISTRY[skill_id]

    if skill_id not in skills:
        skills[skill_id] = {
            "enabled": True,
            "config": config or skill.config_schema.copy(),
        }
    else:
        skills[skill_id]["enabled"] = True
        if config:
            skills[skill_id]["config"].update(config)

    save_user_skills(user_id, skills)
    logger.info(f"Enabled skill {skill_id} for user {user_id}")
    return True


def disable_skill(user_id: str, skill_id: str) -> bool:
    """Disable a skill for a user."""
    skills = load_user_skills(user_id)
    if skill_id in skills:
        skills[skill_id]["enabled"] = False
        save_user_skills(user_id, skills)
        logger.info(f"Disabled skill {skill_id} for user {user_id}")
        return True
    return False


def get_user_enabled_skills(user_id: str) -> List[str]:
    """Get list of enabled skill IDs for a user."""
    skills = load_user_skills(user_id)
    return [sid for sid, data in skills.items() if data.get("enabled", False)]


def get_user_skill_config(user_id: str, skill_id: str) -> Dict:
    """Get config for a specific skill for a user."""
    skills = load_user_skills(user_id)
    if skill_id in skills:
        return skills[skill_id].get("config", {})
    return {}


def update_skill_config(user_id: str, skill_id: str, config: Dict) -> bool:
    """Update config for a specific skill."""
    skills = load_user_skills(user_id)
    if skill_id in skills:
        skills[skill_id]["config"].update(config)
        save_user_skills(user_id, skills)
        return True
    return False


# ── Avatar Skill Execution ──────────────────────────────────────────────────

def get_avatar_active_skills(avatar_key: str, user_id: str) -> List[Dict]:
    """Get active skills for an avatar for a specific user.

    Returns list of {skill_id, skill_name, config} for enabled skills
    that this avatar supports.
    """
    enabled = get_user_enabled_skills(user_id)
    avatar_skills = get_skills_for_avatar(avatar_key)

    active = []
    for skill in avatar_skills:
        if skill.id in enabled:
            config = get_user_skill_config(user_id, skill.id)
            active.append({
                "skill_id": skill.id,
                "skill_name": skill.name,
                "description": skill.description,
                "category": skill.category.value,
                "config": config,
                "requires_google": skill.requires_google,
                "requires_ig": skill.requires_ig,
            })
    return active


def check_skill_permissions(user_id: str, skill_id: str, user_has_google: bool = False,
                            user_has_ig: bool = False) -> Dict:
    """Check if user has required permissions for a skill."""
    skill = SKILL_REGISTRY.get(skill_id)
    if not skill:
        return {"ok": False, "error": "Skill not found"}

    missing = []
    if skill.requires_google and not user_has_google:
        missing.append("google_oauth")
    if skill.requires_ig and not user_has_ig:
        missing.append("instagram_linked")

    if missing:
        return {
            "ok": False,
            "error": f"Missing permissions: {', '.join(missing)}",
            "missing": missing,
        }
    return {"ok": True}
