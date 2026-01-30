"""
Authentication API endpoints.

Provides:
- POST /api/auth/login - Authenticate user and get session token
- GET /api/auth/me - Get current user info
- POST /api/auth/logout - Cognito Global Sign-Out
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, status, Header

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
from app.services.cognito_admin import global_signout_user

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
