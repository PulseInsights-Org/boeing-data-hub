"""
Authentication module for Boeing Data Hub.

Provides simple session-based authentication with:
- Token generation and validation
- User session management
- FastAPI dependency for protected routes
"""

import base64
import time
import logging
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# Security scheme for Bearer token
security = HTTPBearer(auto_error=False)

# Session timeout in seconds (24 hours)
SESSION_TIMEOUT = 24 * 60 * 60

# In-memory session store (for simple implementation)
# In production, consider using Redis for distributed sessions
active_sessions: dict[str, dict] = {}

def generate_session_token(user_id: str, username: str) -> str:
    """
    Generate a session token for authenticated user.

    Token format: base64(user_id:username:expiry_timestamp)
    """
    expiry = int(time.time()) + SESSION_TIMEOUT
    token_data = f"{user_id}:{username}:{expiry}"
    token = base64.b64encode(token_data.encode()).decode()

    # Store session
    active_sessions[token] = {
        "user_id": user_id,
        "username": username,
        "expiry": expiry,
        "created_at": int(time.time())
    }

    logger.info(f"Session created for user {username} (expires in 24h)")
    return token

def validate_session_token(token: str) -> Optional[dict]:
    """
    Validate a session token and return user info if valid.

    Returns None if token is invalid or expired.
    """
    try:
        # Check if token exists in active sessions
        if token in active_sessions:
            session = active_sessions[token]

            # Check expiry
            if session["expiry"] > int(time.time()):
                return {
                    "user_id": session["user_id"],
                    "username": session["username"]
                }
            else:
                # Token expired, remove from sessions
                del active_sessions[token]
                logger.info(f"Session expired for user {session['username']}")
                return None

        # Try to decode token (for cases where server restarted)
        decoded = base64.b64decode(token.encode()).decode()
        parts = decoded.split(":")

        if len(parts) != 3:
            return None

        user_id, username, expiry_str = parts
        expiry = int(expiry_str)

        # Check if token is still valid
        if expiry > int(time.time()):
            # Re-add to active sessions
            active_sessions[token] = {
                "user_id": user_id,
                "username": username,
                "expiry": expiry,
                "created_at": int(time.time())
            }
            return {"user_id": user_id, "username": username}

        return None

    except Exception as e:
        logger.warning(f"Token validation failed: {e}")
        return None

def invalidate_session(token: str) -> bool:
    """
    Invalidate a session token (logout).

    Returns True if session was found and removed.
    """
    if token in active_sessions:
        username = active_sessions[token].get("username", "unknown")
        del active_sessions[token]
        logger.info(f"Session invalidated for user {username}")
        return True
    return False

def get_token_from_header(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[str]:
    """Extract token from Authorization header."""
    if credentials and credentials.scheme.lower() == "bearer":
        return credentials.credentials
    return None

async def get_current_user(
    token: Optional[str] = Depends(get_token_from_header)
) -> dict:
    """
    FastAPI dependency to get the current authenticated user.

    Raises HTTPException 401 if not authenticated.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = validate_session_token(token)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user

async def get_current_user_optional(
    token: Optional[str] = Depends(get_token_from_header)
) -> Optional[dict]:
    """
    FastAPI dependency to optionally get the current user.

    Returns None if not authenticated (doesn't raise exception).
    """
    if not token:
        return None

    return validate_session_token(token)
