"""
AWS Cognito Admin Service

Provides server-side Cognito operations using boto3.
Requires AWS credentials with cognito-idp:GlobalSignOut permission.
"""
import logging
from typing import Dict
import boto3
from botocore.exceptions import ClientError
from app.core.config import get_settings

logger = logging.getLogger(__name__)


def get_cognito_client():
    """
    Get boto3 Cognito Identity Provider client.

    Returns:
        boto3 CognitoIdentityProvider client configured with region from settings
    """
    settings = get_settings()
    return boto3.client('cognito-idp', region_name=settings.cognito_region)


async def global_signout_user(access_token: str) -> Dict[str, any]:
    """
    Perform global sign-out for a user using their access token.

    This revokes all refresh tokens for the user across all devices.
    The access token will remain valid until expiration (typically 1 hour).
    This is expected OAuth2 behavior - JWT access tokens cannot be revoked immediately.

    Args:
        access_token: The Cognito access token for the user

    Returns:
        dict with 'success' boolean and optional 'error' message

    Example:
        >>> result = await global_signout_user("eyJraWQiOiI...")
        >>> if result["success"]:
        ...     print("User logged out globally")
    """
    try:
        client = get_cognito_client()

        # Call GlobalSignOut API
        # This invalidates all refresh tokens for the user
        client.global_sign_out(AccessToken=access_token)

        logger.info("Successfully performed global sign-out")
        return {"success": True}

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        error_msg = e.response.get('Error', {}).get('Message', str(e))

        logger.error(f"Cognito global sign-out failed: {error_code} - {error_msg}")

        # Common errors:
        # - NotAuthorizedException: Token expired or invalid
        # - TooManyRequestsException: Rate limited
        # - InvalidParameterException: Malformed token

        return {
            "success": False,
            "error": f"{error_code}: {error_msg}"
        }

    except Exception as e:
        logger.error(f"Unexpected error during global sign-out: {e}")
        return {
            "success": False,
            "error": str(e)
        }
