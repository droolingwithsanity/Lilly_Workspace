"""
Lilly AI — Admin Token Authentication
Simple auth using a pre-shared admin token instead of Clerk.
"""

import os
import json
import time
import logging
import secrets
from pathlib import Path
from typing import Optional, Dict, Any
import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────
ADMIN_TOKEN = os.environ.get("OPENCONNECTOR_ADMIN_TOKEN", "")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "laurencekidney@gmail.com")


def is_owner(user: Optional[Dict[str, Any]]) -> bool:
    if not user:
        return False
    return user.get("email", "") == OWNER_EMAIL


def user_permissions(user: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    if is_owner(user):
        return {
            "read": True,
            "write": True,
            "admin": True,
            "sensors": True,
            "camera": True,
        }
    return {
        "read": True,
        "write": False,
        "admin": False,
        "sensors": False,
        "camera": True,
    }


# Per-user memory
DATA_DIR = Path(os.environ.get("LILLY_DATA_DIR", "/app/data"))
USER_MEMORY_DIR = DATA_DIR / "users"


async def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """
    Validate the request using the admin token from the Authorization header.
    Returns a user dict with keys: id, email, name, permissions.
    Returns None if not authenticated.
    """
    auth = request.headers.get("authorization", "")
    token = ""
    if auth.startswith("Bearer "):
        token = auth[7:]

    if not token:
        token = request.headers.get("X-Admin-Token", "")

    if not token:
        return None

    if token != ADMIN_TOKEN:
        return None

    return {
        "id": "admin",
        "email": OWNER_EMAIL,
        "name": "Admin",
        "permissions": user_permissions({"email": OWNER_EMAIL}),
    }


# ─── PER-USER MEMORY ─────────────────────────────────────────────
def get_user_memory_path(user_id: str) -> Path:
    user_dir = USER_MEMORY_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "memory.json"


def load_user_memory(user_id: str) -> dict:
    path = get_user_memory_path(user_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"summary": "", "entries": []}


def save_user_memory(user_id: str, data: dict):
    path = get_user_memory_path(user_id)
    path.write_text(json.dumps(data, indent=2))


# ─── GOOGLE OAUTH TOKENS (per-user) ──────────────────────────────
def get_google_token_path(user_id: str) -> Path:
    user_dir = USER_MEMORY_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "google_tokens.json"


def save_google_tokens(user_id: str, tokens: Dict[str, Any]):
    path = get_google_token_path(user_id)
    path.write_text(json.dumps(tokens, indent=2))


def load_google_tokens(user_id: str) -> Optional[Dict[str, Any]]:
    path = get_google_token_path(user_id)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            exp = data.get("expires_at", 0)
            if exp and time.time() > exp - 60:
                return None
            return data
        except Exception:
            pass
    return None


async def fetch_google_tokens_from_clerk(user_id: str) -> Optional[Dict[str, Any]]:
    """Stub — no longer fetching from Clerk. Returns locally cached tokens."""
    return load_google_tokens(user_id)


async def get_google_access_token(user_id: str) -> Optional[str]:
    cached = load_google_tokens(user_id)
    if cached and cached.get("access_token"):
        return cached["access_token"]
    return None


# ─── GMAIL HELPERS ───────────────────────────────────────────────

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


async def gmail_list_messages(
    user_id: str,
    query: str = "is:unread",
    max_results: int = 10,
) -> list:
    token = await get_google_access_token(user_id)
    if not token:
        logger.warning(f"gmail_list_messages: no Google token for {user_id}")
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{GMAIL_API}/users/me/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={"q": query, "maxResults": max_results},
            )
            if r.status_code != 200:
                logger.warning(f"Gmail list failed: {r.status_code}")
                return []
            ids = [m["id"] for m in r.json().get("messages", [])]
            if not ids:
                return []

            async def _fetch(mid: str) -> Optional[dict]:
                resp = await client.get(
                    f"{GMAIL_API}/users/me/messages/{mid}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "format": "metadata",
                        "metadataHeaders": ["Subject", "From", "Date"],
                    },
                )
                if resp.status_code == 200:
                    d = resp.json()
                    headers = {
                        h["name"]: h["value"]
                        for h in d.get("payload", {}).get("headers", [])
                    }
                    return {
                        "id": mid,
                        "subject": headers.get("Subject", "(no subject)"),
                        "from": headers.get("From", ""),
                        "date": headers.get("Date", ""),
                        "snippet": d.get("snippet", ""),
                    }
                return None

            import asyncio

            results = await asyncio.gather(*[_fetch(mid) for mid in ids])
            return [m for m in results if m]
    except Exception as e:
        logger.warning(f"gmail_list_messages error: {e}")
        return []


# ─── GOOGLE CALENDAR HELPERS ─────────────────────────────────────

CALENDAR_API = "https://www.googleapis.com/calendar/v3"


async def calendar_list_events(
    user_id: str,
    max_results: int = 10,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
) -> list:
    token = await get_google_access_token(user_id)
    if not token:
        logger.warning(f"calendar_list_events: no Google token for {user_id}")
        return []

    import datetime as _dt

    now = _dt.datetime.utcnow()
    if not time_min:
        time_min = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if not time_max:
        time_max = (now + _dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{CALENDAR_API}/calendars/primary/events",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "maxResults": max_results,
                    "orderBy": "startTime",
                    "singleEvents": "true",
                    "timeMin": time_min,
                    "timeMax": time_max,
                },
            )
            if r.status_code != 200:
                logger.warning(f"Calendar list failed: {r.status_code}")
                return []
            items = r.json().get("items", [])
            events = []
            for item in items:
                start = item.get("start", {})
                events.append(
                    {
                        "id": item.get("id", ""),
                        "summary": item.get("summary", "(no title)"),
                        "start": start.get("dateTime") or start.get("date", ""),
                        "end": (
                            item.get("end", {}).get("dateTime")
                            or item.get("end", {}).get("date", "")
                        ),
                        "location": item.get("location", ""),
                        "description": (item.get("description") or "")[:200],
                    }
                )
            return events
    except Exception as e:
        logger.warning(f"calendar_list_events error: {e}")
        return []


# ─── INIT ─────────────────────────────────────────────────────────
async def init_clerk_auth():
    """
    Initialize the admin-token auth system.
    Call this once at app startup.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USER_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if not ADMIN_TOKEN:
        logger.warning(
            "OPENCONNECTOR_ADMIN_TOKEN not set — all requests will be rejected. "
            "Add it to your .env file."
        )
        return
    logger.info("Admin-token auth initialised.")
