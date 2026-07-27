"""
Lilly AI — Simple Email/Password Authentication
Per-user sessions with JWT tokens and isolated memory.
"""
import os, json, hashlib, secrets, time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 72
DATA_DIR = Path(os.environ.get("LILLY_DATA_DIR", "/app/data"))
USERS_FILE = DATA_DIR / "users.json"
USER_MEMORY_DIR = DATA_DIR / "users"
GOOGLE_TOKENS_FILE = DATA_DIR / "google_tokens.json"

# ─── PASSWORD HASHING ───────────────────────────────────────────
def _hash_password(password: str, salt: str = None) -> str:
    """Hash password with PBKDF2-SHA256."""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}:{dk.hex()}"

def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored hash."""
    salt, hash_hex = stored.split(":", 1)
    return _hash_password(password, salt) == stored

# ─── JWT TOKENS ──────────────────────────────────────────────────
try:
    import jwt as pyjwt
except ImportError:
    pyjwt = None

def create_token(user_id: str, email: str, name: str) -> str:
    """Create a JWT access token."""
    now = datetime.utcnow()
    payload = {
        "sub": user_id,
        "email": email,
        "name": name,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    if pyjwt:
        return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    # Fallback: base64 JSON token (no library needed)
    import base64
    serializable = {k: v.isoformat() if isinstance(v, datetime) else v for k, v in payload.items()}
    return base64.urlsafe_b64encode(json.dumps(serializable).encode()).decode()

def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify JWT token and return payload."""
    if not token:
        return None
    try:
        if pyjwt:
            return pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        # Fallback decode
        import base64
        payload = json.loads(base64.urlsafe_b64decode(token.encode()))
        # Check expiry
        exp = payload.get("exp", 0)
        if isinstance(exp, str):
            exp = datetime.fromisoformat(exp).timestamp()
        if time.time() > exp:
            return None
        return payload
    except Exception as e:
        logger.debug(f"Token verification failed: {e}")
        return None

# ─── USER STORE ──────────────────────────────────────────────────
@dataclass
class User:
    id: str
    email: str
    name: str
    password_hash: str
    created_at: str
    role: str = "user"

class UserStore:
    """Persistent user storage with file backend."""

    def __init__(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        USER_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self._users: Dict[str, dict] = {}
        self._load()

    def _load(self):
        if USERS_FILE.exists():
            try:
                self._users = json.loads(USERS_FILE.read_text())
            except Exception:
                self._users = {}

    def _save(self):
        USERS_FILE.write_text(json.dumps(self._users, indent=2))

    def create_user(self, email: str, password: str, name: str) -> Optional[User]:
        """Register a new user. Returns None if email already exists."""
        email_lower = email.lower().strip()
        # Check duplicate
        for u in self._users.values():
            if u["email"].lower() == email_lower:
                return None

        user_id = secrets.token_hex(8)
        now = datetime.utcnow().isoformat()
        user = User(
            id=user_id,
            email=email_lower,
            name=name.strip(),
            password_hash=_hash_password(password),
            created_at=now,
        )
        self._users[user_id] = asdict(user)
        # Remove password hash from stored dict for safety? No — we need it for login.
        self._save()

        # Create user memory directory
        user_dir = USER_MEMORY_DIR / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "memory.json").write_text(json.dumps({"summary": "", "entries": []}, indent=2))

        logger.info(f"Created user: {email_lower} (id={user_id})")
        return user

    def authenticate(self, email: str, password: str) -> Optional[User]:
        """Authenticate by email+password. Returns User or None."""
        email_lower = email.lower().strip()
        for u in self._users.values():
            if u["email"].lower() == email_lower:
                if _verify_password(password, u["password_hash"]):
                    return User(**{k: v for k, v in u.items() if k != "password_hash"})
                return None
        return None

    def get_user(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        u = self._users.get(user_id)
        if not u:
            return None
        return User(**{k: v for k, v in u.items() if k != "password_hash"})

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        email_lower = email.lower().strip()
        for u in self._users.values():
            if u["email"].lower() == email_lower:
                return User(**{k: v for k, v in u.items() if k != "password_hash"})
        return None

    def create_google_user(self, email: str, name: str, google_id: str) -> Optional[User]:
        """Create a user from Google OAuth (no password)."""
        email_lower = email.lower().strip()
        for u in self._users.values():
            if u["email"].lower() == email_lower:
                return User(**{k: v for k, v in u.items() if k != "password_hash"})

        user_id = secrets.token_hex(8)
        now = datetime.utcnow().isoformat()
        user = User(
            id=user_id,
            email=email_lower,
            name=name.strip(),
            password_hash="google:oauth",
            created_at=now,
        )
        self._users[user_id] = asdict(user)
        self._save()

        user_dir = USER_MEMORY_DIR / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "memory.json").write_text(json.dumps({"summary": "", "entries": []}, indent=2))

        logger.info(f"Created Google user: {email_lower} (id={user_id})")
        return user

    # ─── GOOGLE TOKENS ─────────────────────────────────────────
    def _load_google_tokens(self) -> dict:
        if GOOGLE_TOKENS_FILE.exists():
            try:
                return json.loads(GOOGLE_TOKENS_FILE.read_text())
            except Exception:
                pass
        return {}

    def _save_google_tokens(self, data: dict):
        GOOGLE_TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
        GOOGLE_TOKENS_FILE.write_text(json.dumps(data, indent=2))

    def save_google_tokens(self, user_id: str, tokens: dict):
        """Save Google OAuth tokens for a user."""
        all_tokens = self._load_google_tokens()
        all_tokens[user_id] = tokens
        self._save_google_tokens(all_tokens)

    def get_google_tokens(self, user_id: str) -> Optional[dict]:
        """Get Google OAuth tokens for a user."""
        all_tokens = self._load_google_tokens()
        return all_tokens.get(user_id)

    def list_users(self) -> list:
        """List all users (without password hashes)."""
        return [
            {k: v for k, v in u.items() if k != "password_hash"}
            for u in self._users.values()
        ]

# ─── PER-USER MEMORY ─────────────────────────────────────────────
def get_user_memory_path(user_id: str) -> Path:
    """Get the memory file path for a user."""
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


# ─── MODULE COMPATIBILITY WRAPPERS ─────────────────────────────
# ─── COMPATIBILITY WRAPPERS ─────────────────────────────────────

def get_google_tokens(user_id: str) -> Optional[dict]:
    """Module-level wrapper for Google OAuth token retrieval."""
    return get_user_store().get_google_tokens(user_id)
def get_google_tokens(user_id: str) -> Optional[dict]:
    """Get Google OAuth tokens for a user."""
    return get_user_store().get_google_tokens(user_id)

def save_google_tokens(user_id: str, tokens: dict):
    """Save Google OAuth tokens for a user."""
    return get_user_store().save_google_tokens(user_id, tokens)


# ─── GLOBALS ─────────────────────────────────────────────────────
user_store: Optional[UserStore] = None

def init_auth() -> UserStore:
    """Initialize the auth system."""
    global user_store
    user_store = UserStore()
    logger.info(f"Auth system initialized ({len(user_store._users)} users)")
    return user_store

def get_user_store() -> UserStore:
    global user_store
    if user_store is None:
        user_store = UserStore()
    return user_store
