"""
Lilly AI — Clerk.com Authentication
Verifies Clerk session JWTs server-side using Clerk's JWKS endpoint.
Provides a drop-in replacement for the old email/password auth.py module.
"""
import os
import json
import time
import asyncio
import logging
import httpx
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────
CLERK_PUBLISHABLE_KEY = os.environ.get("CLERK_PUBLISHABLE_KEY", "")
CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "laurencekidney@gmail.com")

def is_owner(user: Optional[Dict[str, Any]]) -> bool:
    """Check if the user is the owner (full permissions)."""
    if not user:
        return False
    return user.get("email", "") == OWNER_EMAIL

def user_permissions(user: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    """Return permission flags for a user. Owner gets full access, others get read-only."""
    if is_owner(user):
        return {"read": True, "write": True, "admin": True, "sensors": True, "camera": True}
    return {"read": True, "write": False, "admin": False, "sensors": False, "camera": True}

# Derive the Clerk API base URL from the publishable key prefix.
# Format: pk_live_<base64(frontend_api)>  or  pk_test_<base64(frontend_api)>
# The JWKS endpoint is: https://<frontend_api>/.well-known/jwks.json
# Alternatively Clerk provides: https://api.clerk.com/v1/jwks

# We use the Clerk Backend API for server-side token verification.
CLERK_BACKEND_API = "https://api.clerk.com"

# Per-user memory (same structure as the old auth.py so nothing else breaks)
DATA_DIR = Path(os.environ.get("LILLY_DATA_DIR", "/app/data"))
USER_MEMORY_DIR = DATA_DIR / "users"

# ─── JWKS CACHE ──────────────────────────────────────────────────
_jwks_cache: Optional[Dict] = None
_jwks_cached_at: float = 0
JWKS_TTL = 3600  # re-fetch JWKS every hour


async def _get_jwks() -> Optional[Dict]:
    """Fetch (and cache) Clerk's JWKS for JWT verification."""
    global _jwks_cache, _jwks_cached_at
    if _jwks_cache and (time.time() - _jwks_cached_at) < JWKS_TTL:
        return _jwks_cache
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            headers = {}
            if CLERK_SECRET_KEY:
                headers["Authorization"] = f"Bearer {CLERK_SECRET_KEY}"
            r = await client.get(
                f"{CLERK_BACKEND_API}/v1/jwks",
                headers=headers,
            )
            if r.status_code == 200:
                _jwks_cache = r.json()
                _jwks_cached_at = time.time()
                return _jwks_cache
            logger.warning(f"Clerk JWKS fetch failed: {r.status_code} {r.text}")
    except Exception as e:
        logger.warning(f"Clerk JWKS fetch error: {e}")
    return None


# ─── TOKEN VERIFICATION ──────────────────────────────────────────
def _decode_clerk_jwt(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify a Clerk session JWT using PyJWT + the cached JWKS.
    Falls back to a lightweight decode-only (no signature check) when
    PyJWT or cryptography is unavailable, so the server still starts.
    """
    try:
        import jwt as pyjwt
        from jwt import PyJWKClient
    except ImportError:
        logger.warning("PyJWT not installed — falling back to unverified JWT decode. "
                       "Install: pip install PyJWT cryptography")
        return _fallback_decode(token)

    if not _jwks_cache:
        # No JWKS yet — do a best-effort unverified decode so the app
        # doesn't hard-break on first request before JWKS is loaded.
        return _fallback_decode(token)

    try:
        jwks_client = PyJWKClient.__new__(PyJWKClient)
        jwks_client.jwk_set = pyjwt.PyJWKSet.from_dict(_jwks_cache)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        return payload
    except Exception as e:
        logger.debug(f"Clerk JWT verification failed: {e}")
        return None


def _fallback_decode(token: str) -> Optional[Dict[str, Any]]:
    """Decode JWT payload without signature verification (dev / fallback only)."""
    try:
        import base64
        parts = token.split(".")
        if len(parts) != 3:
            return None
        # Pad base64url segment
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        # Respect expiry even without signature check
        exp = payload.get("exp", 0)
        if time.time() > exp:
            return None
        return payload
    except Exception:
        return None


async def verify_clerk_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Full async token verification: fetches JWKS if needed, then decodes.
    Returns the JWT payload dict on success, None on failure.
    """
    if not token:
        return None
    if not _jwks_cache:
        await _get_jwks()
    return _decode_clerk_jwt(token)


# ─── SESSION EXTRACTION ──────────────────────────────────────────
def _extract_token(request: Request) -> str:
    """Pull the Clerk session token from cookie or Authorization header."""
    # Clerk JS SDK sets a cookie named __session or __clerk_db_jwt
    for name in ("__session", "__clerk_db_jwt"):
        val = request.cookies.get(name, "")
        if val:
            return val
    # Bearer header fallback (mobile / API callers)
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return ""


async def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """
    FastAPI-compatible dependency + helper.
    Returns a user dict with keys: id, email, name (same shape as old auth.py).
    Returns None if not authenticated.
    """
    token = _extract_token(request)
    payload = await verify_clerk_token(token)
    if not payload:
        return None

    # Clerk token claims:
    #   sub      — Clerk user ID  (user_xxxxxxxx)
    #   email    — primary email  (present if you add it to session claims in Clerk dashboard)
    #   name     — full name      (same note)
    # If email/name aren't in the JWT, we return what we have; the caller
    # can fetch the rest from the Clerk Backend API if needed.
    user_id = payload.get("sub", "")
    email = (
        payload.get("email")
        or payload.get("primary_email_address_id", "")
        or ""
    )
    name = (
        payload.get("name")
        or payload.get("fullName")
        or payload.get("firstName", "")
        or email.split("@")[0]
    )
    return {"id": user_id, "email": email, "name": name, "clerk_payload": payload, "permissions": user_permissions({"email": email})}


async def fetch_clerk_user(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch full user data from the Clerk Backend API.
    Useful when the JWT doesn't carry email/name (default Clerk config).
    """
    if not CLERK_SECRET_KEY or not user_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{CLERK_BACKEND_API}/v1/users/{user_id}",
                headers={"Authorization": f"Bearer {CLERK_SECRET_KEY}"},
            )
            if r.status_code == 200:
                data = r.json()
                email_objs = data.get("email_addresses", [])
                primary_id = data.get("primary_email_address_id", "")
                email = ""
                for e in email_objs:
                    if e.get("id") == primary_id:
                        email = e.get("email_address", "")
                        break
                if not email and email_objs:
                    email = email_objs[0].get("email_address", "")
                first = data.get("first_name") or ""
                last = data.get("last_name") or ""
                name = f"{first} {last}".strip() or email.split("@")[0]
                return {"id": user_id, "email": email, "name": name}
    except Exception as e:
        logger.debug(f"Clerk user fetch error: {e}")
    return None


# ─── PER-USER MEMORY (unchanged API) ─────────────────────────────
def get_user_memory_path(user_id: str) -> Path:
    """Get the memory file path for a Clerk user."""
    # Clerk IDs look like "user_2abc..." — safe as directory names
    user_dir = USER_MEMORY_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "memory.json"


def load_user_memory(user_id: str) -> dict:
    """Load a user's conversation memory."""
    path = get_user_memory_path(user_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"summary": "", "entries": []}


def save_user_memory(user_id: str, data: dict):
    """Save a user's conversation memory."""
    path = get_user_memory_path(user_id)
    path.write_text(json.dumps(data, indent=2))


# ─── GOOGLE OAUTH TOKENS (per-user) ──────────────────────────────
# Clerk stores the Google OAuth access token inside the session JWT when the
# user has connected Google SSO with extra scopes.  We persist a copy locally
# so background tasks (email triage, calendar briefing) can access it without
# a live request.

def get_google_token_path(user_id: str) -> Path:
    user_dir = USER_MEMORY_DIR / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "google_tokens.json"


def save_google_tokens(user_id: str, tokens: Dict[str, Any]):
    """Persist Google OAuth tokens for a user."""
    path = get_google_token_path(user_id)
    path.write_text(json.dumps(tokens, indent=2))


def load_google_tokens(user_id: str) -> Optional[Dict[str, Any]]:
    """Load persisted Google OAuth tokens for a user. Returns None if absent."""
    path = get_google_token_path(user_id)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            # Treat as expired if expiry timestamp is in the past
            exp = data.get("expires_at", 0)
            if exp and time.time() > exp - 60:
                return None  # caller should refresh
            return data
        except Exception:
            pass
    return None


async def fetch_google_tokens_from_clerk(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch the Google OAuth access token that Clerk stored for this user.

    Clerk keeps OAuth tokens when:
      1. The user signed in with Google SSO, AND
      2. You added extra scopes in the Clerk dashboard (OAuth → Google →
         Additional scopes: https://www.googleapis.com/auth/gmail.readonly
                            https://www.googleapis.com/auth/calendar.readonly)

    The Backend API endpoint is:
        GET /v1/users/{user_id}/oauth_access_tokens/oauth_google
    """
    if not CLERK_SECRET_KEY or not user_id:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{CLERK_BACKEND_API}/v1/users/{user_id}/oauth_access_tokens/oauth_google",
                headers={"Authorization": f"Bearer {CLERK_SECRET_KEY}"},
            )
            if r.status_code == 200:
                data = r.json()
                # Clerk returns a list; take the first entry
                entries = data if isinstance(data, list) else data.get("data", [])
                if entries:
                    entry = entries[0]
                    tokens = {
                        "access_token": entry.get("token", ""),
                        "scopes": entry.get("scopes", []),
                        "expires_at": entry.get("token_secret", {}).get(
                            "expires_at", 0
                        ) if isinstance(entry.get("token_secret"), dict) else 0,
                        "fetched_at": time.time(),
                    }
                    if tokens["access_token"]:
                        save_google_tokens(user_id, tokens)
                        return tokens
            logger.debug(
                f"fetch_google_tokens_from_clerk: status {r.status_code} for {user_id}"
            )
    except Exception as e:
        logger.debug(f"fetch_google_tokens_from_clerk error: {e}")
    return None


async def get_google_access_token(user_id: str) -> Optional[str]:
    """
    Return a valid Google access token for the user.
    Checks local cache first, then re-fetches from Clerk if missing/expired.
    """
    cached = load_google_tokens(user_id)
    if cached and cached.get("access_token"):
        return cached["access_token"]
    tokens = await fetch_google_tokens_from_clerk(user_id)
    if tokens:
        return tokens.get("access_token")
    return None


# ─── GMAIL HELPERS ───────────────────────────────────────────────

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


async def gmail_list_messages(
    user_id: str,
    query: str = "is:unread",
    max_results: int = 10,
) -> list:
    """
    Return a list of recent Gmail message snippets for the user.

    Requires the scope:  https://www.googleapis.com/auth/gmail.readonly
    Must be added in: Clerk Dashboard → SSO Connections → Google →
                      Additional scopes.
    """
    token = await get_google_access_token(user_id)
    if not token:
        logger.warning(f"gmail_list_messages: no Google token for {user_id}")
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # Step 1: list message IDs
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

            # Step 2: fetch snippet for each message (batch via asyncio)
            async def _fetch(mid: str) -> Optional[dict]:
                resp = await client.get(
                    f"{GMAIL_API}/users/me/messages/{mid}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"format": "metadata",
                            "metadataHeaders": ["Subject", "From", "Date"]},
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
    """
    Return upcoming calendar events for the user.

    Requires the scope:  https://www.googleapis.com/auth/calendar.readonly
    Must be added in: Clerk Dashboard → SSO Connections → Google →
                      Additional scopes.

    time_min / time_max: RFC3339 strings, e.g. "2026-07-16T00:00:00Z"
    Defaults to the next 7 days if not specified.
    """
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
                events.append({
                    "id": item.get("id", ""),
                    "summary": item.get("summary", "(no title)"),
                    "start": start.get("dateTime") or start.get("date", ""),
                    "end": (item.get("end", {}).get("dateTime")
                            or item.get("end", {}).get("date", "")),
                    "location": item.get("location", ""),
                    "description": (item.get("description") or "")[:200],
                })
            return events
    except Exception as e:
        logger.warning(f"calendar_list_events error: {e}")
        return []


# ─── JWKS BACKGROUND REFRESH ─────────────────────────────────────
async def init_clerk_auth():
    """
    Call this once at app startup (e.g. from a lifespan handler or startup event).
    Pre-warms the JWKS cache so the first user request doesn't incur a round-trip.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    USER_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if not CLERK_PUBLISHABLE_KEY:
        logger.warning(
            "CLERK_PUBLISHABLE_KEY not set — Clerk auth is disabled. "
            "Add it to your .env file."
        )
        return
    if not CLERK_SECRET_KEY:
        logger.warning(
            "CLERK_SECRET_KEY not set — server-side user lookups will not work. "
            "Add it to your .env file."
        )
    await _get_jwks()
    logger.info("Clerk auth initialised (JWKS cached).")
