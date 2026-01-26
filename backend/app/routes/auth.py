"""
Authentication API endpoints.

Provides:
- POST /api/auth/login - Authenticate user and get session token
- GET /api/auth/me - Get current user info
- POST /api/auth/logout - Invalidate session
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, status

from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    User,
    LogoutResponse,
)
from app.core.auth import (
    generate_session_token,
    invalidate_session,
    get_current_user,
    get_token_from_header,
    SESSION_TIMEOUT,
)
from app.db.user_store import get_user_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Authenticate user with username and password.

    Returns a session token valid for 24 hours.
    """
    user_store = get_user_store()

    # Validate credentials
    user = user_store.validate_credentials(request.username, request.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Generate session token
    token = generate_session_token(user["id"], user["username"])

    # Update last login time
    user_store.update_last_login(user["id"])

    logger.info(f"User {request.username} logged in successfully")

    return LoginResponse(
        token=token,
        user_id=user["id"],
        username=user["username"],
        expires_in=SESSION_TIMEOUT,
        message="Login successful"
    )


@router.get("/me", response_model=User)
async def get_me(current_user: dict = Depends(get_current_user)):
    """
    Get current authenticated user info.

    Requires valid session token in Authorization header.
    """
    return User(
        user_id=current_user["user_id"],
        username=current_user["username"]
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(token: str = Depends(get_token_from_header)):
    """
    Logout and invalidate session token.

    Returns success even if token was already invalid.
    """
    if token:
        invalidate_session(token)
        logger.info("User logged out")

    return LogoutResponse(
        message="Logged out successfully",
        success=True
    )
