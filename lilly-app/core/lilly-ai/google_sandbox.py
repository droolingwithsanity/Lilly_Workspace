"""Google Account Sandboxing System.

Each Google account is isolated to its own directory. No cross-user data
leakage. OAuth tokens, user data, and avatar memories are all per-user.
"""

import json
import time
import hashlib
import logging
from pathlib import Path
from typing import Dict, Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger("GoogleSandbox")

DATA_DIR = Path(__file__).parent.parent.parent / "data"
USERS_DIR = DATA_DIR / "users"
SANDBOX_INDEX = DATA_DIR / "sandbox_index.json"


def _hash_google_id(google_sub: str) -> str:
    """Create a sandboxed user ID from Google sub.

    Uses a stable hash so the same Google account always maps to the same
    directory, but the raw Google ID is never exposed in filesystem paths.
    """
    salt = "lilly-ai-sandbox-v1"
    return hashlib.sha256(f"{salt}:{google_sub}".encode()).hexdigest()[:16]


def get_sandboxed_user_id(google_sub: str) -> str:
    """Get or create sandboxed user ID for a Google account."""
    user_id = _hash_google_id(google_sub)
    _update_index(google_sub, user_id)
    return user_id


def _update_index(google_sub: str, user_id: str) -> None:
    """Maintain a secure index mapping Google subs to sandboxed IDs."""
    index = {}
    try:
        if SANDBOX_INDEX.exists():
            index = json.loads(SANDBOX_INDEX.read_text())
    except Exception:
        pass

    if google_sub not in index:
        index[google_sub] = {
            "user_id": user_id,
            "created": time.time(),
        }
        try:
            SANDBOX_INDEX.write_text(json.dumps(index, indent=2))
        except Exception as e:
            logger.error(f"Failed to update sandbox index: {e}")


def get_user_dir(user_id: str) -> Path:
    """Get the sandboxed directory for a user."""
    user_dir = USERS_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def get_user_google_tokens(user_id: str) -> Optional[Dict]:
    """Load Google OAuth tokens for a user."""
    path = get_user_dir(user_id) / "google_tokens.json"
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception as e:
        logger.warning(f"Failed to load Google tokens for {user_id}: {e}")
    return None


def save_user_google_tokens(user_id: str, tokens: Dict) -> None:
    """Save Google OAuth tokens for a user."""
    path = get_user_dir(user_id) / "google_tokens.json"
    try:
        path.write_text(json.dumps(tokens, indent=2))
    except Exception as e:
        logger.error(f"Failed to save Google tokens: {e}")


def user_has_google(user_id: str) -> bool:
    """Check if user has connected Google account."""
    tokens = get_user_google_tokens(user_id)
    return tokens is not None and "access_token" in tokens


def get_user_ig_handles(user_id: str) -> List[str]:
    """Get Instagram handles linked to this user."""
    profile_path = get_user_dir(user_id) / "profile.json"
    try:
        if profile_path.exists():
            profile = json.loads(profile_path.read_text())
            return profile.get("linked_ig_handles", [])
    except Exception:
        pass
    return []


def link_ig_handle(user_id: str, ig_handle: str) -> None:
    """Link an Instagram handle to a user."""
    from persistent_memory import load_user_profile, save_user_profile
    profile = load_user_profile(user_id)
    if ig_handle not in profile.linked_ig_handles:
        profile.linked_ig_handles.append(ig_handle)
        profile.ig_connected = True
        save_user_profile(profile)


def check_sandbox_isolation(user_id_a: str, user_id_b: str) -> bool:
    """Verify two users are properly isolated."""
    if user_id_a == user_id_b:
        return True
    dir_a = get_user_dir(user_id_a)
    dir_b = get_user_dir(user_id_b)
    return dir_a != dir_b and not str(dir_a).startswith(str(dir_b)) and not str(dir_b).startswith(str(dir_a))


def get_user_session_data(user_id: str) -> Dict:
    """Get all session-relevant data for a user (sandboxed)."""
    from persistent_memory import load_user_profile, load_avatar_memory
    from avatar_skills import load_user_skills

    profile = load_user_profile(user_id)
    skills = load_user_skills(user_id)
    memories = {}
    for avatar in ["puppy", "fox", "cat", "bear", "bunny", "owl", "deer", "wolf", "raccoon"]:
        mem = load_avatar_memory(user_id, avatar)
        if mem.entries or mem.preferences:
            memories[avatar] = {
                "entries": len(mem.entries),
                "preferences": mem.preferences,
                "projects": list(mem.projects.keys()),
                "habits": list(mem.habits.keys()),
            }

    return {
        "user_id": user_id,
        "profile": {
            "name": profile.name,
            "email": profile.email,
            "display_name": profile.display_name,
            "timezone": profile.timezone,
            "google_connected": profile.google_connected,
            "ig_connected": profile.ig_connected,
            "linked_handles": profile.linked_ig_handles,
        },
        "skills": skills,
        "memories": memories,
    }


def list_all_sandboxed_users() -> List[Dict]:
    """List all sandboxed users (admin only)."""
    if not USERS_DIR.exists():
        return []

    users = []
    for user_dir in USERS_DIR.iterdir():
        if not user_dir.is_dir():
            continue
        profile_path = user_dir / "profile.json"
        if not profile_path.exists():
            continue
        try:
            profile = json.loads(profile_path.read_text())
            users.append({
                "user_id": user_dir.name,
                "name": profile.get("name", ""),
                "email": profile.get("email", ""),
                "created": profile.get("created_at", 0),
                "last_seen": profile.get("last_seen", 0),
            })
        except Exception:
            continue

    return sorted(users, key=lambda u: u.get("last_seen", 0), reverse=True)
