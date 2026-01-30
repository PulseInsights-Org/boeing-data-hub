"""
Test cases for Boeing Data Hub logout endpoint and Cognito global sign-out functionality.
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi import status
from botocore.exceptions import ClientError


@pytest.mark.asyncio
async def test_logout_with_valid_token(client, auth_headers):
    """Test logout endpoint with valid Bearer token."""
    with patch('app.services.cognito_admin.get_cognito_client') as mock_get_client:
        # Mock boto3 client
        mock_client = MagicMock()
        mock_client.global_sign_out.return_value = {}
        mock_get_client.return_value = mock_client

        response = client.post("/api/auth/logout", headers=auth_headers)

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success"] is True
        assert data["global_signout_success"] is True
        assert "all devices" in data["message"].lower()


@pytest.mark.asyncio
async def test_logout_without_authorization_header(client):
    """Test logout endpoint without Authorization header."""
    response = client.post("/api/auth/logout")

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert "detail" in data or ("error" in data and not data.get("success", True))


@pytest.mark.asyncio
async def test_logout_with_invalid_authorization_format(client):
    """Test logout endpoint with invalid Authorization header format."""
    response = client.post(
        "/api/auth/logout",
        headers={"Authorization": "InvalidFormat token123"}
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_logout_with_expired_cognito_token(client, mock_cognito_token):
    """Test logout endpoint with expired Cognito token."""
    with patch('app.services.cognito_admin.get_cognito_client') as mock_get_client:
        # Mock boto3 client to raise NotAuthorizedException
        mock_client = MagicMock()
        error_response = {
            'Error': {
                'Code': 'NotAuthorizedException',
                'Message': 'Access Token has expired'
            }
        }
        mock_client.global_sign_out.side_effect = ClientError(error_response, 'GlobalSignOut')
        mock_get_client.return_value = mock_client

        response = client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {mock_cognito_token}"}
        )

        # Should still return success but with global_signout_success=False
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success"] is True
        assert data["global_signout_success"] is False


@pytest.mark.asyncio
async def test_logout_with_rate_limit_error(client, auth_headers):
    """Test logout endpoint when Cognito rate limits the request."""
    with patch('app.services.cognito_admin.get_cognito_client') as mock_get_client:
        mock_client = MagicMock()
        error_response = {
            'Error': {
                'Code': 'TooManyRequestsException',
                'Message': 'Rate limit exceeded'
            }
        }
        mock_client.global_sign_out.side_effect = ClientError(error_response, 'GlobalSignOut')
        mock_get_client.return_value = mock_client

        response = client.post("/api/auth/logout", headers=auth_headers)

        # Should still return success (graceful degradation)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success"] is True
        assert data["global_signout_success"] is False


@pytest.mark.asyncio
async def test_cognito_admin_global_signout_success():
    """Test cognito_admin.global_signout_user function with successful response."""
    from app.services.cognito_admin import global_signout_user

    mock_token = "valid_access_token"

    with patch('app.services.cognito_admin.get_cognito_client') as mock_get_client:
        mock_client = MagicMock()
        mock_client.global_sign_out.return_value = {}
        mock_get_client.return_value = mock_client

        result = await global_signout_user(mock_token)

        assert result["success"] is True
        assert "error" not in result
        mock_client.global_sign_out.assert_called_once_with(AccessToken=mock_token)


@pytest.mark.asyncio
async def test_cognito_admin_global_signout_invalid_token():
    """Test cognito_admin.global_signout_user with invalid token."""
    from app.services.cognito_admin import global_signout_user

    mock_token = "invalid_token"

    with patch('app.services.cognito_admin.get_cognito_client') as mock_get_client:
        mock_client = MagicMock()
        error_response = {
            'Error': {
                'Code': 'InvalidParameterException',
                'Message': 'Invalid token format'
            }
        }
        mock_client.global_sign_out.side_effect = ClientError(error_response, 'GlobalSignOut')
        mock_get_client.return_value = mock_client

        result = await global_signout_user(mock_token)

        assert result["success"] is False
        assert "error" in result
        assert "InvalidParameterException" in result["error"]


@pytest.mark.asyncio
async def test_logout_response_schema():
    """Test that logout response matches expected schema."""
    from app.schemas.auth import LogoutResponse

    # Test successful logout response
    response = LogoutResponse(
        success=True,
        message="Logged out successfully from all devices",
        global_signout_success=True
    )

    assert response.success is True
    assert response.global_signout_success is True
    assert isinstance(response.message, str)

    # Test failed global signout but successful local logout
    response_failed = LogoutResponse(
        success=True,
        message="Logged out locally (global sign-out unavailable)",
        global_signout_success=False
    )

    assert response_failed.success is True
    assert response_failed.global_signout_success is False


@pytest.mark.asyncio
async def test_logout_with_network_error(client, auth_headers):
    """Test logout endpoint when network error occurs during Cognito API call."""
    with patch('app.services.cognito_admin.get_cognito_client') as mock_get_client:
        mock_client = MagicMock()
        mock_client.global_sign_out.side_effect = Exception("Network timeout")
        mock_get_client.return_value = mock_client

        response = client.post("/api/auth/logout", headers=auth_headers)

        # Should still return success (graceful degradation)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["success"] is True
        assert data["global_signout_success"] is False
