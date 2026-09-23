"""Enhanced Persistent Memory System - per-user, per-avatar memory.

Each Google account is sandboxed to its own directory. Memory persists
across sessions and is shared only between the user and their assigned avatars.
"""

import json
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("PersistentMemory")

DATA_DIR = Path(__file__).parent.parent.parent / "data"
USERS_DIR = DATA_DIR / "users"


@dataclass
class UserProfile:
    user_id: str
    email: str = ""
    name: str = ""
    display_name: str = ""
    avatar_url: str = ""
    timezone: str = "UTC"
    created_at: float = 0.0
    last_seen: float = 0.0
    linked_ig_handles: List[str] = field(default_factory=list)
    preferences: Dict = field(default_factory=dict)
    google_connected: bool = False
    ig_connected: bool = False


@dataclass
class AvatarMemory:
    user_id: str
    avatar_key: str
    entries: List[Dict] = field(default_factory=list)
    summaries: List[Dict] = field(default_factory=list)
    preferences: Dict = field(default_factory=dict)
    projects: Dict = field(default_factory=dict)
    habits: Dict = field(default_factory=dict)
    last_updated: float = 0.0
    total_interactions: int = 0


def _user_dir(user_id: str) -> Path:
    user_dir = USERS_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def load_user_profile(user_id: str) -> UserProfile:
    path = _user_dir(user_id) / "profile.json"
    try:
        if path.exists():
            return UserProfile(**json.loads(path.read_text()))
    except Exception as e:
        logger.warning(f"Failed to load profile for {user_id}: {e}")
    return UserProfile(user_id=user_id, created_at=time.time())


def save_user_profile(profile: UserProfile) -> None:
    path = _user_dir(profile.user_id) / "profile.json"
    try:
        profile.last_seen = time.time()
        path.write_text(json.dumps(asdict(profile), indent=2))
    except Exception as e:
        logger.error(f"Failed to save profile: {e}")


def update_user_profile(user_id: str, **kwargs) -> UserProfile:
    profile = load_user_profile(user_id)
    for key, value in kwargs.items():
        if hasattr(profile, key):
            setattr(profile, key, value)
    save_user_profile(profile)
    return profile


def _memory_path(user_id: str, avatar_key: str) -> Path:
    return _user_dir(user_id) / f"memory_{avatar_key}.json"


def load_avatar_memory(user_id: str, avatar_key: str) -> AvatarMemory:
    path = _memory_path(user_id, avatar_key)
    try:
        if path.exists():
            return AvatarMemory(**json.loads(path.read_text()))
    except Exception as e:
        logger.warning(f"Failed to load memory {user_id}/{avatar_key}: {e}")
    return AvatarMemory(user_id=user_id, avatar_key=avatar_key)


def save_avatar_memory(memory: AvatarMemory) -> None:
    path = _memory_path(memory.user_id, memory.avatar_key)
    try:
        memory.last_updated = time.time()
        path.write_text(json.dumps(asdict(memory), indent=2))
    except Exception as e:
        logger.error(f"Failed to save memory: {e}")


def add_memory_entry(user_id: str, avatar_key: str, content: str,
                     category: str = "general", importance: int = 5,
                     tags: Optional[List[str]] = None,
                     context: str = "", source: str = "conversation") -> None:
    memory = load_avatar_memory(user_id, avatar_key)
    entry = {
        "timestamp": time.time(),
        "content": content,
        "category": category,
        "importance": importance,
        "tags": tags or [],
        "context": context,
        "source": source,
    }
    memory.entries.append(entry)
    memory.total_interactions += 1
    if category == "preference":
        key = content.split(":")[0].strip().lower() if ":" in content else tags[0] if tags else content[:30]
        memory.preferences[key] = content
    elif category == "project":
        key = tags[0] if tags else content[:30]
        memory.projects[key] = {"content": content, "updated": time.time()}
    elif category == "habit":
        key = tags[0] if tags else content[:30]
        if key not in memory.habits:
            memory.habits[key] = {"streak": 0, "last_logged": 0, "entries": []}
        memory.habits[key]["entries"].append(time.time())
        memory.habits[key]["last_logged"] = time.time()
    if len(memory.entries) > 500:
        _compact_memory(memory)
    save_avatar_memory(memory)


def _compact_memory(memory: AvatarMemory) -> None:
    old = sorted(memory.entries, key=lambda e: e.get("importance", 5), reverse=True)
    kept = old[:200]
    summary = {
        "timestamp": time.time(),
        "content": f"Compacted {len(memory.entries)} entries into {len(kept)}",
        "categories": list(set(e.get("category", "general") for e in memory.entries)),
        "high_importance": [e for e in memory.entries if e.get("importance", 5) >= 8],
    }
    memory.summaries.append(summary)
    memory.entries = kept


def search_memory(user_id: str, avatar_key: str, query: str,
                  category: Optional[str] = None, limit: int = 10) -> List[Dict]:
    memory = load_avatar_memory(user_id, avatar_key)
    query_lower = query.lower()
    results = []
    for entry in reversed(memory.entries):
        if category and entry.get("category") != category:
            continue
        if query_lower in entry.get("content", "").lower():
            results.append(entry)
            if len(results) >= limit:
                break
    return results


def get_user_context(user_id: str, avatar_key: str) -> str:
    memory = load_avatar_memory(user_id, avatar_key)
    parts = []
    if memory.preferences:
        prefs = "; ".join(f"{k}: {v}" for k, v in list(memory.preferences.items())[:5])
        parts.append(f"Preferences: {prefs}")
    if memory.projects:
        projs = "; ".join(f"{k}" for k in list(memory.projects.keys())[:3])
        parts.append(f"Active projects: {projs}")
    if memory.habits:
        habits = "; ".join(f"{k} (streak: {h.get('streak', 0)})" for k, h in list(memory.habits.items())[:3])
        parts.append(f"Habits: {habits}")
    recent = memory.entries[-5:] if memory.entries else []
    if recent:
        recent_content = "; ".join(e.get("content", "")[:50] for e in recent)
        parts.append(f"Recent: {recent_content}")
    return "\n".join(parts) if parts else ""


def log_habit(user_id: str, avatar_key: str, habit_name: str) -> Dict:
    memory = load_avatar_memory(user_id, avatar_key)
    if habit_name not in memory.habits:
        memory.habits[habit_name] = {"streak": 0, "last_logged": 0, "entries": []}
    habit = memory.habits[habit_name]
    now = time.time()
    last = habit.get("last_logged", 0)
    from datetime import datetime
    today = datetime.now().date().isoformat()
    yesterday = datetime.fromtimestamp(now - 86400).date().isoformat()
    last_date = datetime.fromtimestamp(last).date().isoformat() if last else ""
    if last_date == today:
        save_avatar_memory(memory)
        return {"ok": True, "streak": habit["streak"], "message": "Already logged today"}
    if last_date == yesterday:
        habit["streak"] += 1
    else:
        habit["streak"] = 1
    habit["last_logged"] = now
    habit["entries"].append(now)
    add_memory_entry(user_id, avatar_key, f"Logged habit: {habit_name}",
                     category="habit", tags=[habit_name], source="observation")
    save_avatar_memory(memory)
    return {"ok": True, "streak": habit["streak"]}


def set_preference(user_id: str, avatar_key: str, key: str, value: str) -> None:
    memory = load_avatar_memory(user_id, avatar_key)
    memory.preferences[key] = value
    add_memory_entry(user_id, avatar_key, f"{key}: {value}",
                     category="preference", importance=8, tags=[key])
    save_avatar_memory(memory)


def add_project(user_id: str, avatar_key: str, project_name: str,
                details: str = "") -> None:
    memory = load_avatar_memory(user_id, avatar_key)
    memory.projects[project_name] = {
        "details": details,
        "created": time.time(),
        "updated": time.time(),
        "status": "active",
    }
    add_memory_entry(user_id, avatar_key, f"New project: {project_name} - {details}",
                     category="project", importance=7, tags=[project_name])
    save_avatar_memory(memory)


def update_project(user_id: str, avatar_key: str, project_name: str,
                   details: str = "", status: str = "active") -> None:
    memory = load_avatar_memory(user_id, avatar_key)
    if project_name in memory.projects:
        memory.projects[project_name]["details"] = details or memory.projects[project_name].get("details", "")
        memory.projects[project_name]["updated"] = time.time()
        memory.projects[project_name]["status"] = status
    add_memory_entry(user_id, avatar_key, f"Updated project: {project_name}",
                     category="project", tags=[project_name])
    save_avatar_memory(memory)


def list_users() -> List[str]:
    if not USERS_DIR.exists():
        return []
    return [d.name for d in USERS_DIR.iterdir() if d.is_dir() and (d / "profile.json").exists()]


def get_user_stats(user_id: str) -> Dict:
    stats = {"avatars": {}, "total_entries": 0, "total_interactions": 0}
    for avatar in ["puppy", "fox", "cat", "bear", "bunny", "owl", "deer", "wolf", "raccoon"]:
        memory = load_avatar_memory(user_id, avatar)
        stats["avatars"][avatar] = {
            "entries": len(memory.entries),
            "interactions": memory.total_interactions,
            "preferences": len(memory.preferences),
            "projects": len(memory.projects),
            "habits": len(memory.habits),
        }
        stats["total_entries"] += len(memory.entries)
        stats["total_interactions"] += memory.total_interactions
    return stats
