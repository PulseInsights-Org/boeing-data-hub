"""
AWS Cognito JWT Validation Module for Boeing Data Hub

Handles fetching JWKS and validating Cognito access tokens.
Tokens are passed from Aviation Gateway via SSO flow.
"""
import time
import logging
from typing import Optional
import httpx
from jose import jwt, jwk, JWTError
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Cache for JWKS keys
_jwks_cache: dict = {}
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 3600  # 1 hour


async def get_jwks() -> dict:
    """
    Fetch and cache JWKS from Cognito.

    Returns the JWKS keys, using cached version if available and not expired.
    """
    global _jwks_cache, _jwks_cache_time

    settings = get_settings()
    current_time = time.time()

    # Return cached keys if still valid
    if _jwks_cache and (current_time - _jwks_cache_time) < JWKS_CACHE_TTL:
        return _jwks_cache

    # Fetch fresh JWKS
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(settings.cognito_jwks_url, timeout=10.0)
            response.raise_for_status()
            _jwks_cache = response.json()
            _jwks_cache_time = current_time
            logger.info("Successfully fetched Cognito JWKS")
            return _jwks_cache
    except Exception as e:
        logger.error(f"Failed to fetch Cognito JWKS: {e}")
        # Return cached keys if fetch fails (better than nothing)
        if _jwks_cache:
            logger.warning("Using stale JWKS cache")
            return _jwks_cache
        raise


def get_signing_key(token: str, jwks: dict) -> Optional[dict]:
    """
    Get the signing key for a token from JWKS.

    Args:
        token: The JWT token
        jwks: The JWKS containing public keys

    Returns:
        The signing key dict or None if not found
    """
    try:
        # Get the key ID from the token header
        headers = jwt.get_unverified_headers(token)
        kid = headers.get("kid")

        if not kid:
            logger.warning("Token missing 'kid' header")
            return None

        # Find the matching key
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return key

        logger.warning(f"No matching key found for kid: {kid}")
        return None
    except JWTError as e:
        logger.error(f"Error extracting token headers: {e}")
        return None


async def verify_cognito_token(token: str) -> dict:
    """
    Verify a Cognito access token and return its claims.

    Args:
        token: The JWT access token from Cognito

    Returns:
        The decoded token claims

    Raises:
        JWTError: If token verification fails
    """
    settings = get_settings()

    # Get JWKS
    jwks = await get_jwks()

    # Get the signing key for this token
    signing_key = get_signing_key(token, jwks)
    if not signing_key:
        raise JWTError("Unable to find signing key for token")

    # Convert JWK to PEM for verification
    try:
        public_key = jwk.construct(signing_key)
    except Exception as e:
        logger.error(f"Failed to construct public key: {e}")
        raise JWTError(f"Invalid signing key: {e}")

    # Verify and decode the token
    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=settings.cognito_issuer,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
                "verify_aud": False,  # Cognito access tokens don't have 'aud' claim
            },
        )

        # Validate token_use claim (should be 'access' for access tokens)
        token_use = payload.get("token_use")
        if token_use != "access":
            logger.warning(f"Invalid token_use: {token_use}")
            raise JWTError(f"Invalid token_use: expected 'access', got '{token_use}'")

        # Validate client_id if configured
        if settings.cognito_app_client_id:
            client_id = payload.get("client_id")
            if client_id != settings.cognito_app_client_id:
                logger.warning(f"Client ID mismatch: {client_id}")
                raise JWTError("Token client_id does not match configured app client")

        return payload

    except JWTError:
        raise
    except Exception as e:
        logger.error(f"Token verification failed: {e}")
        raise JWTError(f"Token verification failed: {e}")


def extract_user_info(payload: dict) -> dict:
    """
    Extract user information from Cognito token claims.

    Args:
        payload: The decoded token claims

    Returns:
        User info dict with id, email, and groups
    """
    return {
        "id": payload.get("sub"),
        "username": payload.get("username"),
        "email": payload.get("email"),
        "groups": payload.get("cognito:groups", []),
        "client_id": payload.get("client_id"),
        "scope": payload.get("scope", "").split(),
    }
