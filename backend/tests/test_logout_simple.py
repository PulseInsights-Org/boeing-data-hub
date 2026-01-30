"""
Simplified test cases for Boeing Data Hub logout endpoint.
"""
import pytest
from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError


@pytest.mark.asyncio
async def test_cognito_admin_global_signout_success():
    """Test cognito_admin.global_signout_user function with successful response."""
    from app.services.cognito_admin import global_signout_user

    mock_token = "valid_access_token_12345"

    with patch('app.services.cognito_admin.get_cognito_client') as mock_get_client:
        mock_client = MagicMock()
        mock_client.global_sign_out.return_value = {}
        mock_get_client.return_value = mock_client

        result = await global_signout_user(mock_token)

        assert result["success"] is True
        assert "error" not in result
        mock_client.global_sign_out.assert_called_once_with(AccessToken=mock_token)


@pytest.mark.asyncio
async def test_cognito_admin_global_signout_with_expired_token():
    """Test cognito_admin.global_signout_user with expired token."""
    from app.services.cognito_admin import global_signout_user

    mock_token = "expired_token"

    with patch('app.services.cognito_admin.get_cognito_client') as mock_get_client:
        mock_client = MagicMock()
        error_response = {
            'Error': {
                'Code': 'NotAuthorizedException',
                'Message': 'Access Token has expired'
            }
        }
        mock_client.global_sign_out.side_effect = ClientError(error_response, 'GlobalSignOut')
        mock_get_client.return_value = mock_client

        result = await global_signout_user(mock_token)

        assert result["success"] is False
        assert "error" in result
        assert "NotAuthorizedException" in result["error"]


@pytest.mark.asyncio
async def test_cognito_admin_global_signout_with_network_error():
    """Test cognito_admin.global_signout_user with network error."""
    from app.services.cognito_admin import global_signout_user

    mock_token = "valid_token"

    with patch('app.services.cognito_admin.get_cognito_client') as mock_get_client:
        mock_client = MagicMock()
        mock_client.global_sign_out.side_effect = Exception("Connection timeout")
        mock_get_client.return_value = mock_client

        result = await global_signout_user(mock_token)

        assert result["success"] is False
        assert "error" in result
        assert "Connection timeout" in result["error"]


def test_logout_response_schema():
    """Test that LogoutResponse schema works correctly."""
    from app.schemas.auth import LogoutResponse

    # Test successful logout
    response_success = LogoutResponse(
        success=True,
        message="Logged out successfully from all devices",
        global_signout_success=True
    )

    assert response_success.success is True
    assert response_success.global_signout_success is True
    assert "all devices" in response_success.message

    # Test partial success
    response_partial = LogoutResponse(
        success=True,
        message="Logged out locally (global sign-out unavailable)",
        global_signout_success=False
    )

    assert response_partial.success is True
    assert response_partial.global_signout_success is False


def test_get_cognito_client_configuration():
    """Test that get_cognito_client is configured correctly."""
    from app.services.cognito_admin import get_cognito_client

    with patch('app.services.cognito_admin.boto3.client') as mock_boto_client:
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        client = get_cognito_client()

        # Verify boto3.client was called with cognito-idp service
        mock_boto_client.assert_called_once()
        call_args = mock_boto_client.call_args
        assert call_args[0][0] == 'cognito-idp'
        assert 'region_name' in call_args[1]
