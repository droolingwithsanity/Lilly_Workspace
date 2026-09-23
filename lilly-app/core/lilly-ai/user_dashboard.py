"""User Dashboard API - droolingwithsanity.ca

Handles user authentication, skill management, memory viewing,
and scheduled DM configuration. All data sandboxed per Google account.
"""

import json
import time
import logging
from pathlib import Path
from typing import Dict, Optional, List
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

DATA_DIR = Path(__file__).parent.parent.parent / "data"
USERS_DIR = DATA_DIR / "users"


def _user_dir(user_id: str) -> Path:
    user_dir = USERS_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir

logger = logging.getLogger("UserDashboard")

router = APIRouter(prefix="/api/user", tags=["user"])


def _get_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user.get("user_id", "")


def _check_owner(request: Request) -> bool:
    user = getattr(request.state, "user", None)
    if not user:
        return False
    return user.get("is_owner", False)


@router.get("/profile")
async def get_profile(request: Request):
    from persistent_memory import load_user_profile
    user_id = _get_user_id(request)
    profile = load_user_profile(user_id)
    return {
        "user_id": profile.user_id,
        "name": profile.name,
        "email": profile.email,
        "display_name": profile.display_name,
        "timezone": profile.timezone,
        "google_connected": profile.google_connected,
        "ig_connected": profile.ig_connected,
        "linked_handles": profile.linked_ig_handles,
        "preferences": profile.preferences,
    }


@router.post("/profile")
async def update_profile(request: Request, body: Dict):
    from persistent_memory import update_user_profile
    user_id = _get_user_id(request)
    allowed = {"name", "display_name", "timezone", "preferences"}
    updates = {k: v for k, v in body.items() if k in allowed}
    profile = update_user_profile(user_id, **updates)
    return {"ok": True}


@router.get("/skills")
async def get_user_skills(request: Request):
    from avatar_skills import get_all_skills, load_user_skills
    user_id = _get_user_id(request)
    user_skills = load_user_skills(user_id)
    skills = []
    for skill in get_all_skills():
        pref = user_skills.get(skill.id, {})
        skills.append({
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "category": skill.category.value,
            "avatars": skill.avatars,
            "enabled": pref.get("enabled", skill.enabled_by_default),
            "config": pref.get("config", skill.config_schema.copy()),
            "requires_google": skill.requires_google,
            "requires_ig": skill.requires_ig,
        })
    return {"skills": skills}


@router.post("/skills/{skill_id}/enable")
async def enable_skill_endpoint(request: Request, skill_id: str, body: Optional[Dict] = None):
    from avatar_skills import enable_skill, SKILL_REGISTRY, check_skill_permissions
    from google_sandbox import user_has_google, get_user_ig_handles
    user_id = _get_user_id(request)
    if skill_id not in SKILL_REGISTRY:
        raise HTTPException(status_code=404, detail="Skill not found")
    has_google = user_has_google(user_id)
    has_ig = bool(get_user_ig_handles(user_id))
    perms = check_skill_permissions(user_id, skill_id, has_google, has_ig)
    if not perms["ok"]:
        return JSONResponse(status_code=403, content=perms)
    ok = enable_skill(user_id, skill_id, body.get("config") if body else None)
    return {"ok": ok}


@router.post("/skills/{skill_id}/disable")
async def disable_skill_endpoint(request: Request, skill_id: str):
    from avatar_skills import disable_skill
    user_id = _get_user_id(request)
    ok = disable_skill(user_id, skill_id)
    return {"ok": ok}


@router.put("/skills/{skill_id}/config")
async def update_skill_config_endpoint(request: Request, skill_id: str, body: Dict):
    from avatar_skills import update_skill_config
    user_id = _get_user_id(request)
    ok = update_skill_config(user_id, skill_id, body)
    return {"ok": ok}


@router.get("/memory/{avatar_key}")
async def get_avatar_memory(request: Request, avatar_key: str):
    from persistent_memory import load_avatar_memory
    user_id = _get_user_id(request)
    memory = load_avatar_memory(user_id, avatar_key)
    return {
        "avatar": avatar_key,
        "entries": len(memory.entries),
        "preferences": memory.preferences,
        "projects": memory.projects,
        "habits": {k: {"streak": v.get("streak", 0)} for k, v in memory.habits.items()},
        "recent_entries": memory.entries[-10:] if memory.entries else [],
        "total_interactions": memory.total_interactions,
    }


@router.get("/memory/{avatar_key}/search")
async def search_memory_endpoint(request: Request, avatar_key: str, q: str, category: Optional[str] = None):
    from persistent_memory import search_memory
    user_id = _get_user_id(request)
    results = search_memory(user_id, avatar_key, q, category)
    return {"results": results, "count": len(results)}


@router.get("/memory/{avatar_key}/context")
async def get_memory_context(request: Request, avatar_key: str):
    from persistent_memory import get_user_context
    user_id = _get_user_id(request)
    context = get_user_context(user_id, avatar_key)
    return {"context": context}


@router.post("/habits/{habit_name}/log")
async def log_habit_endpoint(request: Request, habit_name: str, avatar_key: str = "puppy"):
    from persistent_memory import log_habit
    user_id = _get_user_id(request)
    result = log_habit(user_id, avatar_key, habit_name)
    return result


@router.get("/habits")
async def get_habits(request: Request, avatar_key: str = "puppy"):
    from persistent_memory import load_avatar_memory
    user_id = _get_user_id(request)
    memory = load_avatar_memory(user_id, avatar_key)
    # Return as array format expected by webui: [{"name": ..., "streak": ...}]
    habits_list = [{"name": k, "streak": v.get("streak", 0) if isinstance(v, dict) else 0}
                   for k, v in (memory.habits or {}).items()]
    return {"habits": habits_list}


@router.post("/projects")
async def add_project_endpoint(request: Request, body: Dict):
    from persistent_memory import add_project
    user_id = _get_user_id(request)
    name = body.get("name", "")
    details = body.get("details", "")
    avatar = body.get("avatar_key", "cat")
    add_project(user_id, avatar, name, details)
    return {"ok": True}


@router.get("/projects")
async def get_projects(request: Request, avatar_key: str = "cat"):
    from persistent_memory import load_avatar_memory
    user_id = _get_user_id(request)
    memory = load_avatar_memory(user_id, avatar_key)
    # Return as array format expected by webui: [{"name": ..., "status": ..., "details": ...}]
    projects_list = []
    for k, v in (memory.projects or {}).items():
        if isinstance(v, dict):
            projects_list.append({
                "name": k,
                "status": v.get("status", "active"),
                "details": v.get("content", v.get("details", "")),
            })
        else:
            projects_list.append({"name": k, "status": "active", "details": str(v)})
    return {"projects": projects_list}


@router.put("/projects/{project_name}")
async def update_project_endpoint(request: Request, project_name: str, body: Dict):
    from persistent_memory import update_project
    user_id = _get_user_id(request)
    details = body.get("details", "")
    status = body.get("status", "active")
    avatar = body.get("avatar_key", "cat")
    update_project(user_id, avatar, project_name, details, status)
    return {"ok": True}


@router.get("/stats")
async def get_user_stats(request: Request):
    from persistent_memory import get_user_stats
    user_id = _get_user_id(request)
    return get_user_stats(user_id)


@router.get("/memory/{avatar_key}/all")
async def get_all_memories(request: Request):
    from persistent_memory import load_avatar_memory
    user_id = _get_user_id(request)
    memories = {}
    for av in ["puppy", "fox", "cat", "bear", "bunny", "owl", "deer", "wolf", "raccoon"]:
        mem = load_avatar_memory(user_id, av)
        if mem.entries or mem.preferences:
            memories[av] = {
                "entries": len(mem.entries),
                "preferences": mem.preferences,
                "projects": list(mem.projects.keys()),
                "habits": list(mem.habits.keys()),
            }
    return {"memories": memories}


@router.get("/admin/users")
async def admin_list_users(request: Request):
    if not _check_owner(request):
        raise HTTPException(status_code=403, detail="Owner only")
    from google_sandbox import list_all_sandboxed_users
    return {"users": list_all_sandboxed_users()}


@router.get("/admin/users/{user_id}/stats")
async def admin_user_stats(request: Request, user_id: str):
    if not _check_owner(request):
        raise HTTPException(status_code=403, detail="Owner only")
    from persistent_memory import get_user_stats
    return get_user_stats(user_id)


@router.post("/schedule-dm")
async def schedule_dm(request: Request, body: Dict):
    from persistent_memory import load_user_profile
    user_id = _get_user_id(request)
    profile = load_user_profile(user_id)
    dm_data = {
        "user_id": user_id,
        "avatar_key": body.get("avatar_key", "puppy"),
        "recipient": body.get("recipient", ""),
        "message": body.get("message", ""),
        "scheduled_time": body.get("scheduled_time", ""),
        "created": time.time(),
    }
    sched_path = _user_dir(user_id) / "scheduled_dms.json"
    try:
        dms = []
        if sched_path.exists():
            dms = json.loads(sched_path.read_text())
        dms.append(dm_data)
        sched_path.write_text(json.dumps(dms, indent=2))
    except Exception as e:
        logger.error(f"Failed to schedule DM: {e}")
        return {"ok": False, "error": str(e)}
    return {"ok": True, "scheduled": dm_data}


@router.get("/schedule-dm")
async def list_scheduled_dms(request: Request):
    user_id = _get_user_id(request)
    sched_path = _user_dir(user_id) / "scheduled_dms.json"
    try:
        if sched_path.exists():
            return {"dms": json.loads(sched_path.read_text())}
    except Exception:
        pass
    return {"dms": []}


@router.delete("/schedule-dm/{index}")
async def delete_scheduled_dm(request: Request, index: int):
    user_id = _get_user_id(request)
    sched_path = _user_dir(user_id) / "scheduled_dms.json"
    try:
        if sched_path.exists():
            dms = json.loads(sched_path.read_text())
            if 0 <= index < len(dms):
                dms.pop(index)
                sched_path.write_text(json.dumps(dms, indent=2))
                return {"ok": True}
    except Exception:
        pass
    return {"ok": False}
