"""
Auth service — Cognito user management operations.

Auth Service — Cognito authentication operations.

Provides server-side Cognito operations using boto3.
Requires AWS credentials with cognito-idp:GlobalSignOut permission.
Version: 1.0.0
"""
import logging
from typing import Dict

import boto3
from botocore.exceptions import ClientError
from app.core.config import get_settings


logger = logging.getLogger(__name__)


class AuthService:
    @staticmethod
    def _get_cognito_client():
        """
        Get boto3 Cognito Identity Provider client.

        Returns:
            boto3 CognitoIdentityProvider client configured with region from settings
        """
        settings = get_settings()
        return boto3.client('cognito-idp', region_name=settings.cognito_region)

    @staticmethod
    async def global_signout_user(access_token: str) -> Dict[str, any]:
        """
        Perform global sign-out for a user using their access token.

        This revokes all refresh tokens for the user across all devices.
        The access token will remain valid until expiration (typically 1 hour).
        """
        try:
            client = AuthService._get_cognito_client()

            client.global_sign_out(AccessToken=access_token)

            logger.info("Successfully performed global sign-out")
            return {"success": True}

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))

            logger.error(f"Cognito global sign-out failed: {error_code} - {error_msg}")

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


# Backward-compat: module-level functions used by routes/auth.py
def get_cognito_client():
    return AuthService._get_cognito_client()


async def global_signout_user(access_token: str) -> Dict[str, any]:
    return await AuthService.global_signout_user(access_token)
