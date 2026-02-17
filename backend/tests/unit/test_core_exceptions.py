"""
Unit tests for the custom exception hierarchy.

Verifies inheritance chains, attribute assignment, and message formatting
for all exception classes in app.core.exceptions.

Version: 1.0.0
"""
import pytest

from app.core.exceptions import (
    BoeingDataHubException,
    RetryableError,
    NonRetryableError,
    ExternalAPIError,
    RateLimitError,
    ValidationError,
    BatchNotFoundError,
    ProductNotFoundError,
    InvalidPartNumberError,
    AuthenticationError,
    ConnectionTimeoutError,
    DatabaseTransientError,
)


pytestmark = pytest.mark.unit


class TestBaseException:
    """Tests for BoeingDataHubException base class."""

    def test_is_exception(self):
        assert issubclass(BoeingDataHubException, Exception)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(BoeingDataHubException):
            raise BoeingDataHubException("test error")

    def test_message_preserved(self):
        exc = BoeingDataHubException("something went wrong")
        assert str(exc) == "something went wrong"


class TestRetryableErrors:
    """Tests for the retryable error branch of the hierarchy."""

    def test_retryable_inherits_from_base(self):
        assert issubclass(RetryableError, BoeingDataHubException)

    def test_external_api_error_inheritance(self):
        assert issubclass(ExternalAPIError, RetryableError)
        assert issubclass(ExternalAPIError, BoeingDataHubException)

    def test_external_api_error_attributes(self):
        exc = ExternalAPIError(service="Shopify", message="timeout", status_code=504)
        assert exc.service == "Shopify"
        assert exc.status_code == 504
        assert "Shopify" in str(exc)
        assert "timeout" in str(exc)

    def test_external_api_error_default_status_code(self):
        exc = ExternalAPIError(service="Boeing", message="fail")
        assert exc.status_code is None

    def test_rate_limit_error_inheritance(self):
        assert issubclass(RateLimitError, RetryableError)
        assert issubclass(RateLimitError, BoeingDataHubException)

    def test_rate_limit_error_attributes(self):
        exc = RateLimitError(service="Shopify", retry_after=120)
        assert exc.service == "Shopify"
        assert exc.retry_after == 120
        assert "120" in str(exc)

    def test_rate_limit_error_default_retry_after(self):
        exc = RateLimitError(service="Boeing")
        assert exc.retry_after == 60

    def test_connection_timeout_error_inheritance(self):
        assert issubclass(ConnectionTimeoutError, RetryableError)

    def test_database_transient_error_inheritance(self):
        assert issubclass(DatabaseTransientError, RetryableError)


class TestNonRetryableErrors:
    """Tests for the non-retryable error branch of the hierarchy."""

    def test_non_retryable_inherits_from_base(self):
        assert issubclass(NonRetryableError, BoeingDataHubException)

    def test_non_retryable_not_retryable(self):
        assert not issubclass(NonRetryableError, RetryableError)

    def test_validation_error_inheritance(self):
        assert issubclass(ValidationError, NonRetryableError)
        assert not issubclass(ValidationError, RetryableError)

    def test_batch_not_found_error_inheritance(self):
        assert issubclass(BatchNotFoundError, NonRetryableError)
        assert not issubclass(BatchNotFoundError, RetryableError)

    def test_product_not_found_error_inheritance(self):
        assert issubclass(ProductNotFoundError, NonRetryableError)
        assert not issubclass(ProductNotFoundError, RetryableError)

    def test_invalid_part_number_error_inheritance(self):
        assert issubclass(InvalidPartNumberError, NonRetryableError)
        assert not issubclass(InvalidPartNumberError, RetryableError)

    def test_authentication_error_inheritance(self):
        assert issubclass(AuthenticationError, NonRetryableError)
        assert not issubclass(AuthenticationError, RetryableError)


class TestExceptionCatchPatterns:
    """Tests for catch patterns used by Celery task autoretry."""

    def test_retryable_catches_external_api_error(self):
        exc = ExternalAPIError(service="Boeing", message="500")
        assert isinstance(exc, RetryableError)

    def test_retryable_catches_rate_limit_error(self):
        exc = RateLimitError(service="Shopify")
        assert isinstance(exc, RetryableError)

    def test_non_retryable_does_not_catch_retryable(self):
        exc = ExternalAPIError(service="Boeing", message="500")
        assert not isinstance(exc, NonRetryableError)

    def test_base_catches_everything(self):
        exceptions = [
            RetryableError("r"),
            NonRetryableError("n"),
            ExternalAPIError("svc", "msg"),
            RateLimitError("svc"),
            ValidationError("v"),
            BatchNotFoundError("b"),
            ProductNotFoundError("p"),
            InvalidPartNumberError("i"),
            AuthenticationError("a"),
        ]
        for exc in exceptions:
            assert isinstance(exc, BoeingDataHubException)
