"""
Authentication — JWT/Cognito token verification for route protection.

Authentication Module for Boeing Data Hub

Uses AWS Cognito for authentication via SSO from Aviation Gateway.
Supabase is used only for data operations, not authentication.
Version: 1.0.0
"""
import logging
from typing import Optional, List
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError
from app.core.cognito import verify_cognito_token, extract_user_info

logger = logging.getLogger(__name__)
security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Validate Cognito JWT token and return user data.

    The token is passed from Aviation Gateway via SSO flow.
    Token validation includes:
    - Signature verification using Cognito JWKS
    - Expiry check
    - Issuer validation
    - Token use validation (must be 'access')
    """
    token = credentials.credentials

    logger.debug("Validating Cognito authentication token")
    try:
        # Verify the token with Cognito JWKS
        payload = await verify_cognito_token(token)

        # Extract user info from claims
        user_info = extract_user_info(payload)

        user_id = user_info.get("id")
        if not user_id:
            logger.warning("Authentication failed - missing user ID in token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "UNAUTHORIZED",
                    "message": "Invalid token: missing user ID",
                },
            )

        logger.debug(
            f"Authentication successful - user_id: {user_id}, "
            f"email: {user_info.get('email')}, "
            f"groups: {user_info.get('groups')}"
        )

        return {
            "user_id": user_id,
            "username": user_info.get("username"),
            "email": user_info.get("email"),
            "groups": user_info.get("groups", []),
            "scope": user_info.get("scope", []),
        }

    except JWTError as e:
        logger.warning(f"Authentication failed - JWT error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": f"Invalid or expired token: {str(e)}",
            },
        )
    except Exception as e:
        logger.error(f"Authentication failed - unexpected error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Authentication failed",
            },
        )


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
) -> dict | None:
    """
    Optional authentication - returns None if no token provided.
    """
    if not credentials:
        return None
    return await get_current_user(credentials)


def require_groups(required_groups: List[str]):
    """
    Dependency factory for requiring specific Cognito groups.

    Usage:
        @router.get("/admin-only")
        async def admin_endpoint(user: dict = Depends(require_groups(["admin"]))):
            ...
    """
    async def check_groups(user: dict = Depends(get_current_user)) -> dict:
        user_groups = user.get("groups", [])

        # Check if user has at least one of the required groups
        if not any(group in user_groups for group in required_groups):
            logger.warning(
                f"Access denied - user {user.get('user_id')} lacks required groups. "
                f"Has: {user_groups}, Needs one of: {required_groups}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "FORBIDDEN",
                    "message": f"Access denied. Required groups: {required_groups}",
                },
            )

        return user

    return check_groups


# Pre-configured dependencies for common group requirements
require_admin = require_groups(["admin"])
