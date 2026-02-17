"""
Auth routes — login, logout, user management endpoints.

Authentication routes.

Provides:
- GET  /auth/me     – current user info
- POST /auth/logout – Cognito Global Sign-Out
Version: 1.0.0
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Header, status

from app.schemas.auth import User, LogoutResponse
from app.core.auth import get_current_user
from app.services.auth_service import global_signout_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=User)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current authenticated user info."""
    return User(
        user_id=current_user["user_id"],
        username=current_user.get("username"),
        email=current_user.get("email"),
        groups=current_user.get("groups", []),
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(authorization: str = Header(None)):
    """Logout: Cognito global sign-out."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    access_token = authorization.replace("Bearer ", "")
    result = await global_signout_user(access_token)

    if result["success"]:
        return LogoutResponse(
            success=True,
            message="Logged out successfully from all devices",
            global_signout_success=True,
        )

    logger.warning(f"Global sign-out failed: {result.get('error')}")
    return LogoutResponse(
        success=True,
        message="Logged out locally (global sign-out unavailable)",
        global_signout_success=False,
    )
