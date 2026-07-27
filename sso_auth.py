"""
SSO Authentication System for Lilly AI
Manages user sessions, permissions, and OAuth token refresh.
"""
import os
import jwt
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Configuration
JWT_SECRET = os.environ.get("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "24"))
REFRESH_TOKEN_EXPIRY_DAYS = int(os.environ.get("REFRESH_TOKEN_EXPIRY_DAYS", "30"))

# User roles and permissions
ROLES = {
    "admin": ["*"],  # Full access
    "user": [
        "email:read",
        "email:send",
        "email:draft",
        "calendar:read",
        "calendar:write",
        "profile:read"
    ],
    "viewer": [
        "email:read",
        "calendar:read",
        "profile:read"
    ]
}


@dataclass
class UserSession:
    """User session data."""
    user_id: str
    email: str
    name: str
    role: str
    permissions: List[str]
    created_at: str
    expires_at: str
    refresh_expires_at: str
    oauth_tokens: Dict[str, Any]  # Gmail/Outlook tokens
    last_active: str


class SSOTokenManager:
    """Manages JWT tokens and session state."""
    
    def __init__(self, data_dir: str = "/app/data/sso"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_file = self.data_dir / "sessions.json"
        self.revoked_file = self.data_dir / "revoked_tokens.json"
        self._load_state()
    
    def _load_state(self):
        """Load persisted state."""
        self.sessions = {}
        self.revoked_tokens = set()
        
        if self.sessions_file.exists():
            try:
                with open(self.sessions_file) as f:
                    self.sessions = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load sessions: {e}")
        
        if self.revoked_file.exists():
            try:
                with open(self.revoked_file) as f:
                    self.revoked_tokens = set(json.load(f))
            except Exception as e:
                logger.error(f"Failed to load revoked tokens: {e}")
    
    def _save_state(self):
        """Persist state to disk."""
        try:
            with open(self.sessions_file, 'w') as f:
                json.dump(self.sessions, f, indent=2)
            
            with open(self.revoked_file, 'w') as f:
                json.dump(list(self.revoked_tokens), f)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")
    
    def create_tokens(self, user_id: str, email: str, name: str, role: str, 
                     oauth_tokens: Dict[str, Any] = None) -> Dict[str, str]:
        """Create access and refresh tokens."""
        now = datetime.utcnow()
        
        # Get permissions for role
        permissions = ROLES.get(role, ROLES["user"])
        
        # Create access token
        access_payload = {
            "sub": user_id,
            "email": email,
            "name": name,
            "role": role,
            "permissions": permissions,
            "iat": now,
            "exp": now + timedelta(hours=JWT_EXPIRY_HOURS),
            "type": "access"
        }
        access_token = jwt.encode(access_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        
        # Create refresh token
        refresh_payload = {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRY_DAYS),
            "type": "refresh"
        }
        refresh_token = jwt.encode(refresh_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        
        # Store session
        session = UserSession(
            user_id=user_id,
            email=email,
            name=name,
            role=role,
            permissions=permissions,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(hours=JWT_EXPIRY_HOURS)).isoformat(),
            refresh_expires_at=(now + timedelta(days=REFRESH_TOKEN_EXPIRY_DAYS)).isoformat(),
            oauth_tokens=oauth_tokens or {},
            last_active=now.isoformat()
        )
        self.sessions[user_id] = asdict(session)
        self._save_state()
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": JWT_EXPIRY_HOURS * 3600,
            "user": {
                "id": user_id,
                "email": email,
                "name": name,
                "role": role
            }
        }
    
    def validate_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Validate a JWT token and return payload."""
        if token in self.revoked_tokens:
            return None
        
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            
            # Update last active
            user_id = payload.get("sub")
            if user_id and user_id in self.sessions:
                self.sessions[user_id]["last_active"] = datetime.utcnow().isoformat()
                self._save_state()
            
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("Token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.error(f"Invalid token: {e}")
            return None
    
    def refresh_access_token(self, refresh_token: str) -> Optional[Dict[str, str]]:
        """Refresh an access token using a refresh token."""
        payload = self.validate_token(refresh_token)
        
        if not payload or payload.get("type") != "refresh":
            return None
        
        user_id = payload.get("sub")
        session_data = self.sessions.get(user_id)
        
        if not session_data:
            return None
        
        # Revoke old tokens
        self.revoke_token(refresh_token)
        
        # Create new tokens
        return self.create_tokens(
            user_id=user_id,
            email=session_data["email"],
            name=session_data["name"],
            role=session_data["role"],
            oauth_tokens=session_data.get("oauth_tokens", {})
        )
    
    def revoke_token(self, token: str):
        """Revoke a token."""
        self.revoked_tokens.add(token)
        self._save_state()
    
    def revoke_all_user_tokens(self, user_id: str):
        """Revoke all tokens for a user."""
        self.sessions.pop(user_id, None)
        self._save_state()
    
    def check_permission(self, user_id: str, permission: str) -> bool:
        """Check if user has a specific permission."""
        session = self.sessions.get(user_id)
        if not session:
            return False
        
        user_permissions = session.get("permissions", [])
        
        # Admin has all permissions
        if "*" in user_permissions:
            return True
        
        return permission in user_permissions
    
    def get_user_session(self, user_id: str) -> Optional[UserSession]:
        """Get user session data."""
        session_data = self.sessions.get(user_id)
        if not session_data:
            return None
        
        # Check if expired
        expires_at = datetime.fromisoformat(session_data["expires_at"])
        if datetime.utcnow() > expires_at:
            self.sessions.pop(user_id, None)
            self._save_state()
            return None
        
        return UserSession(**session_data)
    
    def update_oauth_tokens(self, user_id: str, oauth_tokens: Dict[str, Any]):
        """Update OAuth tokens for a user."""
        if user_id in self.sessions:
            self.sessions[user_id]["oauth_tokens"] = oauth_tokens
            self._save_state()
    
    def get_oauth_tokens(self, user_id: str) -> Dict[str, Any]:
        """Get OAuth tokens for a user."""
        session = self.sessions.get(user_id)
        return session.get("oauth_tokens", {}) if session else {}
    
    def cleanup_expired_sessions(self):
        """Remove expired sessions."""
        now = datetime.utcnow()
        expired = []
        
        for user_id, session_data in self.sessions.items():
            expires_at = datetime.fromisoformat(session_data["expires_at"])
            if now > expires_at:
                expired.append(user_id)
        
        for user_id in expired:
            self.sessions.pop(user_id, None)
        
        if expired:
            self._save_state()
            logger.info(f"Cleaned up {len(expired)} expired sessions")


class OAuthTokenRefresher:
    """Handles OAuth token refresh for Gmail/Outlook."""
    
    def __init__(self, sso_manager: SSOTokenManager):
        self.sso = sso_manager
    
    async def refresh_gmail_token(self, user_id: str) -> bool:
        """Refresh Gmail OAuth token for a user."""
        tokens = self.sso.get_oauth_tokens(user_id)
        
        if not tokens or "gmail" not in tokens:
            return False
        
        gmail_tokens = tokens["gmail"]
        
        # Check if token needs refresh (within 5 minutes of expiry)
        expires_at = gmail_tokens.get("expires_at")
        if expires_at:
            expires_dt = datetime.fromisoformat(expires_at)
            if datetime.utcnow() < expires_dt - timedelta(minutes=5):
                return True  # Token still valid
        
        # TODO: Implement actual Gmail OAuth token refresh
        # This would call Google's OAuth endpoint with the refresh_token
        # For now, return False to indicate refresh needed
        logger.warning(f"Gmail token refresh not implemented for user {user_id}")
        return False
    
    async def refresh_outlook_token(self, user_id: str) -> bool:
        """Refresh Outlook OAuth token for a user."""
        tokens = self.sso.get_oauth_tokens(user_id)
        
        if not tokens or "outlook" not in tokens:
            return False
        
        outlook_tokens = tokens["outlook"]
        
        # Check if token needs refresh
        expires_at = outlook_tokens.get("expires_at")
        if expires_at:
            expires_dt = datetime.fromisoformat(expires_at)
            if datetime.utcnow() < expires_dt - timedelta(minutes=5):
                return True  # Token still valid
        
        # TODO: Implement actual Outlook OAuth token refresh
        logger.warning(f"Outlook token refresh not implemented for user {user_id}")
        return False


class SSOAuthenticationMiddleware:
    """FastAPI middleware for SSO authentication."""
    
    def __init__(self, sso_manager: SSOTokenManager):
        self.sso = sso_manager
    
    def extract_token(self, authorization: str) -> Optional[str]:
        """Extract token from Authorization header."""
        if not authorization:
            return None
        
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
        
        return None
    
    def authenticate_request(self, authorization: str) -> Optional[Dict[str, Any]]:
        """Authenticate a request and return user info."""
        token = self.extract_token(authorization)
        if not token:
            return None
        
        return self.sso.validate_token(token)
    
    def require_permission(self, permission: str):
        """Decorator to require a specific permission."""
        def decorator(func):
            async def wrapper(*args, **kwargs):
                # Get authorization from request
                request = kwargs.get('request')
                if not request:
                    for arg in args:
                        if hasattr(arg, 'headers'):
                            request = arg
                            break
                
                if not request:
                    return {"error": "No request context"}
                
                auth_header = request.headers.get("authorization", "")
                user_info = self.authenticate_request(auth_header)
                
                if not user_info:
                    return {"error": "Unauthorized"}, 401
                
                user_id = user_info.get("sub")
                if not self.sso.check_permission(user_id, permission):
                    return {"error": "Forbidden", "required": permission}, 403
                
                # Add user info to kwargs
                kwargs["current_user"] = user_info
                return await func(*args, **kwargs)
            
            return wrapper
        return decorator


# Global SSO manager instance
sso_manager = None
oauth_refresher = None
sso_middleware = None


def init_sso(data_dir: str = "/app/data/sso"):
    """Initialize SSO system."""
    global sso_manager, oauth_refresher, sso_middleware
    
    sso_manager = SSOTokenManager(data_dir)
    oauth_refresher = OAuthTokenRefresher(sso_manager)
    sso_middleware = SSOAuthenticationMiddleware(sso_manager)
    
    logger.info("SSO system initialized")
    return sso_manager


# API helpers for authentication
def get_current_user(authorization: str) -> Optional[Dict[str, Any]]:
    """Get current user from authorization header."""
    if not sso_middleware:
        return None
    return sso_middleware.authenticate_request(authorization)


def require_auth(permission: str = None):
    """Decorator for requiring authentication."""
    def decorator(func):
        async def wrapper(request: Request, *args, **kwargs):
            auth_header = request.headers.get("authorization", "")
            user_info = get_current_user(auth_header)
            
            if not user_info:
                return JSONResponse(
                    status_code=401,
                    content={"error": "Authentication required"}
                )
            
            if permission and not sso_manager.check_permission(user_info["sub"], permission):
                return JSONResponse(
                    status_code=403,
                    content={"error": "Insufficient permissions", "required": permission}
                )
            
            kwargs["current_user"] = user_info
            return await func(request, *args, **kwargs)
        
        return wrapper
    return decorator
