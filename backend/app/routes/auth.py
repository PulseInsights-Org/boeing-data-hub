"""
Authentication API endpoints.

Provides:
- GET /api/auth/me - Get current user info from Cognito token
- POST /api/auth/logout - Cognito Global Sign-Out

Authentication is handled via SSO through Aviation Gateway.
Tokens are Cognito JWTs validated against Cognito JWKS.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Header, status

from app.schemas.auth import User, LogoutResponse
from app.core.auth import get_current_user
from app.services.cognito_admin import global_signout_user

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


@router.post("/logout", response_model=LogoutResponse)
async def logout(authorization: str = Header(None)):
    """
    Logout endpoint that performs Cognito global sign-out.

    Extracts the access token from Authorization header and calls
    Cognito's GlobalSignOut API to revoke all refresh tokens.

    Returns success even if global sign-out fails (graceful degradation).
    The client will clear local tokens regardless of the result.

    Args:
        authorization: Bearer token from Authorization header

    Returns:
        LogoutResponse with success status and message

    Raises:
        HTTPException: 401 if Authorization header is missing or invalid
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header"
        )

    # Extract access token
    access_token = authorization.replace("Bearer ", "")

    # Perform global sign-out
    result = await global_signout_user(access_token)

    if result["success"]:
        logger.info("Logout successful with global sign-out")
        return LogoutResponse(
            success=True,
            message="Logged out successfully from all devices",
            global_signout_success=True
        )
    else:
        # Log the error but still return success
        # Client will clear local tokens regardless
        logger.warning(f"Global sign-out failed but allowing logout: {result.get('error')}")
        return LogoutResponse(
            success=True,
            message="Logged out locally (global sign-out unavailable)",
            global_signout_success=False
        )
