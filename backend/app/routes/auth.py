"""
Authentication API endpoints.

Provides:
- GET /api/auth/me - Get current user info from Cognito token

Authentication is handled via SSO through Aviation Gateway.
Tokens are Cognito JWTs validated against Cognito JWKS.
"""

import logging
from fastapi import APIRouter, Depends

from app.schemas.auth import User
from app.core.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me", response_model=User)
async def get_me(current_user: dict = Depends(get_current_user)):
    """
    Get current authenticated user info.

    Requires valid Cognito JWT token in Authorization header.
    Token is obtained via SSO flow through Aviation Gateway.
    """
    return User(
        user_id=current_user["user_id"],
        username=current_user.get("username"),
        email=current_user.get("email"),
        groups=current_user.get("groups", [])
    )
