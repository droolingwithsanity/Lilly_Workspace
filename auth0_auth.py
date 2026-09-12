"""
Auth0 authentication for FastAPI.
Provides proper OIDC login, per-user sessions,
and per-user data isolation (memory, contacts, profile, etc).
"""

import json
import os
import time
import logging
import httpx
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

from auth0_server_python.auth_server.server_client import ServerClient
from auth0_server_python.auth_types import (
    LogoutOptions,
    StartInteractiveLoginOptions,
    StateData,
    TransactionData,
)
from auth0_server_python.store.abstract import AbstractDataStore
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

load_dotenv()

logger = logging.getLogger("auth0")

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.environ.get("AUTH0_CLIENT_SECRET", "")
AUTH0_SECRET = os.environ.get("AUTH0_SECRET", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8098")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "")
ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("AUTH0_ADMIN_EMAILS", "").split(",")
    if e.strip()
}
if OWNER_EMAIL:
    ADMIN_EMAILS.add(OWNER_EMAIL.strip().lower())

DATA_DIR = Path(os.environ.get("LILLY_DATA_DIR", "/app/data"))
USERS_DIR = DATA_DIR / "users"

AUTH_AVAILABLE = bool(
    AUTH0_DOMAIN and AUTH0_CLIENT_ID and AUTH0_CLIENT_SECRET and AUTH0_SECRET
)


class FastAPICookieStore(AbstractDataStore):
    """Encrypted cookie store using FastAPI's Response.set_cookie."""

    def __init__(self, secret: str, cookie_name: str, max_age: int, model):
        super().__init__({"secret": secret})
        self.cookie_name = cookie_name
        self.max_age = max_age
        self.model = model

    async def set(self, identifier, state, remove_if_expires=False, options=None):
        response = options.get("response") if options else None
        if response is None:
            return
        data = state.model_dump() if hasattr(state, "model_dump") else state
        is_https = APP_BASE_URL.startswith("https://")
        response.set_cookie(
            key=self.cookie_name,
            value=self.encrypt(identifier, data),
            httponly=True,
            # SameSite=None required for the transaction cookie to survive the
            # cross-origin redirect from Auth0 back to our callback URL.
            # Must be Secure=True when SameSite=None.
            samesite="none" if is_https else "lax",
            secure=is_https,
            max_age=self.max_age,
            path="/",
        )

    async def get(self, identifier, options=None):
        try:
            request = options.get("request") if options else None
            if request is None:
                return None
            encrypted = request.cookies.get(self.cookie_name)
            if not encrypted:
                return None
            return self.model.model_validate(self.decrypt(identifier, encrypted))
        except Exception:
            return None

    async def delete(self, identifier, options=None):
        response = options.get("response") if options else None
        if response is not None:
            response.delete_cookie(self.cookie_name, path="/")


def create_auth0_client() -> ServerClient:
    return ServerClient(
        domain=AUTH0_DOMAIN,
        client_id=AUTH0_CLIENT_ID,
        client_secret=AUTH0_CLIENT_SECRET,
        redirect_uri=APP_BASE_URL + "/api/auth0/callback",
        authorization_params={"scope": "openid profile email"},
        secret=AUTH0_SECRET,
        state_store=FastAPICookieStore(AUTH0_SECRET, "_a0_session", 259200, StateData),
        transaction_store=FastAPICookieStore(
            AUTH0_SECRET, "_a0_tx", 600, TransactionData
        ),
    )


_auth0_client: Optional[ServerClient] = None


def get_client() -> ServerClient:
    global _auth0_client
    if _auth0_client is None:
        _auth0_client = create_auth0_client()
    return _auth0_client


# ─── Per-user data helpers ─────────────────────────────────────────


def get_user_data_dir(user_id: str) -> Path:
    path = USERS_DIR / user_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_file(user_id: str, filename: str) -> Path:
    return get_user_data_dir(user_id) / filename


def read_user_json(user_id: str, filename: str, default=None):
    path = get_user_file(user_id, filename)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default or {}
    return default or {}


def write_user_json(user_id: str, filename: str, data):
    path = get_user_file(user_id, filename)
    path.write_text(json.dumps(data, indent=2))


def user_memory_path(user_id: str, avatar: str = "puppy") -> Path:
    return get_user_data_dir(user_id) / f"memory_{avatar}.json"


def load_user_memory(user_id: str, avatar: str = "puppy") -> dict:
    path = user_memory_path(user_id, avatar)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"entries": [], "summary": ""}


def save_user_memory(user_id: str, data: dict):
    path = user_memory_path(user_id, "puppy")
    path.write_text(json.dumps(data, indent=2))


# ─── Daily Memory Segmentation ──────────────────────────────────────
# Each Google account gets per-day memory files so context carries across
# sessions within a day, but old days are archived and never leaked to
# other users. Privacy: files live under <user_id>/ — no cross-user access.

DAILY_MEMORY_KEEP_DAYS = 7  # Rolling window: keep last 7 days of daily memory


def _today_str() -> str:
    """Return today's date as YYYY-MM-DD."""
    return datetime.now().strftime("%Y-%m-%d")


def user_daily_memory_path(user_id: str, avatar: str = "puppy", date: str = "") -> Path:
    """Path for a specific day's memory file: <user_dir>/memory_puppy_2026-09-08.json"""
    if not date:
        date = _today_str()
    safe = avatar.replace("/", "_").replace("..", "_")
    return get_user_data_dir(user_id) / f"memory_{safe}_{date}.json"


def load_user_daily_memory(user_id: str, avatar: str = "puppy", date: str = "") -> dict:
    """Load a specific day's memory. Returns empty if no file exists."""
    path = user_daily_memory_path(user_id, avatar, date)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"entries": [], "summary": ""}


def save_user_daily_memory(
    user_id: str, avatar: str = "puppy", date: str = "", data: dict = None
):
    """Save memory for a specific day."""
    if data is None:
        data = {"entries": [], "summary": ""}
    path = user_daily_memory_path(user_id, avatar, date)
    path.write_text(json.dumps(data, indent=2))


def load_user_recent_memory(user_id: str, avatar: str = "puppy", days: int = 7) -> dict:
    """Load today's memory merged with recent days for context continuity.

    Returns a merged dict with:
    - 'entries': today's entries (for active conversation)
    - 'summary': today's summary
    - 'recent_summaries': list of {date, summary} from recent days
    - 'recent_entries': last 5 entries from yesterday (for handoff context)

    Privacy: all data stays under this user_id's directory.
    """
    today = _today_str()
    today_mem = load_user_daily_memory(user_id, avatar, today)

    recent_summaries = []
    recent_entries = []

    for i in range(1, days + 1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        day_mem = load_user_daily_memory(user_id, avatar, day)
        if day_mem.get("summary"):
            recent_summaries.append({"date": day, "summary": day_mem["summary"]})
        # Grab last 5 entries from yesterday only (for handoff context)
        if i == 1 and day_mem.get("entries"):
            recent_entries = day_mem["entries"][-5:]

    return {
        "entries": today_mem.get("entries", []),
        "summary": today_mem.get("summary", ""),
        "recent_summaries": recent_summaries,
        "recent_entries": recent_entries,
    }


def cleanup_old_daily_memories(
    user_id: str, avatar: str = "puppy", keep_days: int = DAILY_MEMORY_KEEP_DAYS
):
    """Remove daily memory files older than keep_days. Privacy-safe: only touches this user's files."""
    user_dir = get_user_data_dir(user_id)
    safe = avatar.replace("/", "_").replace("..", "_")
    prefix = f"memory_{safe}_"
    cutoff = datetime.now() - timedelta(days=keep_days)
    for f in user_dir.glob(f"{prefix}*.json"):
        try:
            # Extract date from filename: memory_puppy_2026-09-08.json → 2026-09-08
            date_str = f.stem.replace(prefix, "")
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
            if file_date < cutoff:
                f.unlink()
                logger.info(f"Cleaned up old daily memory: {f.name}")
        except (ValueError, OSError):
            pass  # Skip malformed filenames


def is_owner(user: Optional[Dict[str, Any]]) -> bool:
    if not user:
        return False
    return (user.get("email", "") or "").strip().lower() == OWNER_EMAIL.strip().lower()


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


async def get_current_user(
    request: Request, response: Optional[Response] = None
) -> Optional[dict]:
    """Get the authenticated user from the Auth0 session cookie.

    Returns a dict with id, email, name, picture or None.
    Requires request; response is needed for logout flows.
    """
    if not AUTH_AVAILABLE:
        return None
    client = get_client()
    store_opts: Dict[str, Any] = {"request": request}
    if response:
        store_opts["response"] = response
    user_data = await client.get_user(store_opts)
    if not user_data:
        return None
    return {
        "id": user_data.get("sub", user_data.get("user_id", "")),
        "email": user_data.get("email", ""),
        "name": user_data.get("name", user_data.get("nickname", "User")),
        "picture": user_data.get("picture", ""),
    }


# ─── Google OAuth token storage ───────────────────────────────


def get_google_token_path(user_id: str) -> Path:
    return get_user_file(user_id, "google_tokens.json")


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


async def get_google_access_token(user_id: str) -> Optional[str]:
    cached = load_google_tokens(user_id)
    if cached and cached.get("access_token"):
        return cached["access_token"]
    return None


# ─── Gmail helpers ────────────────────────────────────────────

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


# ─── Google Calendar helpers ──────────────────────────────────

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


# ─── Auth0 route factory ─────────────────────────────────────


def add_auth0_routes(app: FastAPI):
    """Attach /api/auth0/* and /api/auth/* endpoints to a FastAPI app."""

    @app.get("/api/auth0/login")
    async def auth0_login(request: Request):
        if not AUTH_AVAILABLE:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "Auth0 not configured — set AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET, AUTH0_SECRET in .env"
                },
            )
        client = get_client()
        resp = RedirectResponse(url="", status_code=302)
        url = await client.start_interactive_login(
            options=StartInteractiveLoginOptions(
                authorization_params=dict(request.query_params),
            ),
            store_options={"request": request, "response": resp},
        )
        resp.headers["location"] = url
        return resp

    @app.get("/api/auth0/callback")
    async def auth0_callback(request: Request):
        if not AUTH_AVAILABLE:
            return JSONResponse(
                status_code=503, content={"error": "Auth0 not configured"}
            )
        resp = RedirectResponse(url="/", status_code=302)
        try:
            await get_client().complete_interactive_login(
                url=str(request.url),
                store_options={"request": request, "response": resp},
            )
        except Exception as e:
            err_msg = str(e)
            logger.exception("Auth0 callback error")
            if "transaction" in err_msg.lower() and "missing" in err_msg.lower():
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "Auth0 callback failed: The transaction is missing.",
                        "hint": "Your browser cache/cookies were cleared or the login session expired. Please restart the login flow from /api/auth0/login.",
                        "restart_url": "/api/auth0/login",
                    },
                )
            return JSONResponse(
                status_code=400, content={"error": f"Auth0 callback failed: {err_msg}"}
            )
        return resp

    @app.get("/api/auth0/logout")
    async def auth0_logout(request: Request):
        resp = RedirectResponse(url="", status_code=302)
        try:
            url = await get_client().logout(
                options=LogoutOptions(return_to=APP_BASE_URL),
                store_options={"request": request, "response": resp},
            )
        except Exception:
            url = f"https://{AUTH0_DOMAIN}/v2/logout?returnTo={APP_BASE_URL}"
        resp.headers["location"] = url
        return resp

    @app.get("/api/auth0/me")
    async def auth0_me(request: Request):
        user = await get_current_user(request)
        if not user:
            return JSONResponse(status_code=401, content={"error": "Not authenticated"})
        return {"user": user}

    @app.get("/api/auth/me")
    async def auth_me(request: Request):
        user = await get_current_user(request)
        if not user:
            # Return 200 with auth status instead of 401 to reduce console noise
            return {"authenticated": False, "user": None}
        return {"authenticated": True, "user": user}

    @app.post("/api/auth/logout")
    async def auth_logout():
        return {"ok": True}

    @app.post("/api/auth/login")
    async def auth_login():
        return RedirectResponse(url="/api/auth0/login")

    @app.post("/api/auth/register")
    async def auth_register():
        return RedirectResponse(url="/api/auth0/login?screen_hint=signup")

    @app.get("/api/auth/google")
    async def google_auth_redirect():
        return RedirectResponse("/")

    @app.get("/api/auth/google/callback")
    async def google_auth_callback_redirect():
        return RedirectResponse("/")

    logger.info("Auth0 routes registered")
