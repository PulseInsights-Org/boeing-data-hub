"""
Custom exception hierarchy for Boeing Data Hub.

Exceptions are categorized as:
- RetryableError: Transient errors that should trigger Celery retry
- NonRetryableError: Permanent errors that should fail immediately

This categorization allows Celery tasks to use:
- autoretry_for=(RetryableError,)
- dont_autoretry_for=(NonRetryableError,)
"""


class BoeingDataHubException(Exception):
    """Base exception for Boeing Data Hub."""
    pass


# ============================================
# RETRYABLE ERRORS - Will trigger Celery retry
# ============================================
class RetryableError(BoeingDataHubException):
    """
    Base class for errors that should trigger retry.

    Use this for transient errors where retrying might succeed:
    - Network timeouts
    - Rate limits (with backoff)
    - Temporary service unavailability
    """
    pass


class ExternalAPIError(RetryableError):
    """
    Error from external API (Boeing, Shopify).

    Typically transient - the external service might recover.
    """
    def __init__(self, service: str, message: str, status_code: int = None):
        self.service = service
        self.status_code = status_code
        super().__init__(f"{service} API error: {message}")


class RateLimitError(RetryableError):
    """
    Rate limit exceeded.

    Should retry after the specified delay.
    """
    def __init__(self, service: str, retry_after: int = 60):
        self.service = service
        self.retry_after = retry_after
        super().__init__(f"{service} rate limited. Retry after {retry_after}s")


class ConnectionTimeoutError(RetryableError):
    """Connection or timeout error - typically transient."""
    pass


class DatabaseTransientError(RetryableError):
    """
    Transient database error.

    Examples: connection pool exhausted, deadlock, temporary unavailability
    """
    pass


# ============================================
# NON-RETRYABLE ERRORS - No automatic retry
# ============================================
class NonRetryableError(BoeingDataHubException):
    """
    Base class for errors that should NOT trigger retry.

    Use this for permanent errors where retrying won't help:
    - Validation failures
    - Missing data
    - Authentication errors (need config fix)
    """
    pass


class ValidationError(NonRetryableError):
    """Invalid input data - retrying won't help."""
    pass


class BatchNotFoundError(NonRetryableError):
    """Batch not found - permanent failure."""
    pass


class ProductNotFoundError(NonRetryableError):
    """Product not found in staging - permanent failure."""
    pass


class InvalidPartNumberError(NonRetryableError):
    """Part number format is invalid."""
    pass


class AuthenticationError(NonRetryableError):
    """
    API authentication failed.

    Needs configuration fix, not retry.
    """
    pass
