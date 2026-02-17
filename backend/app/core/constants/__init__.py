"""
Constants package — re-exports from domain-specific modules.

Centralized business constants for Boeing Data Hub.

Re-exports all constants from domain-specific modules for convenience.

Usage:
    from app.core.constants.pricing import MARKUP_FACTOR
    from app.core.constants.publishing import UOM_MAPPING
    # or import everything:
    from app.core.constants import pricing, publishing, extraction, sync
Version: 1.0.0
"""

from app.core.constants import pricing, publishing, extraction, sync
from app.core.constants.pricing import (
    MARKUP_FACTOR,
    DEFAULT_CURRENCY,
    DEFAULT_CERTIFICATE,
    DEFAULT_CONDITION,
    FALLBACK_IMAGE_URL,
)
from app.core.constants.extraction import (
    DEFAULT_SUPPLIER,
    DEFAULT_VENDOR,
    SYSTEM_USER_ID,
    BOEING_BATCH_SIZE,
)
from app.core.constants.publishing import (
    PRODUCT_CATEGORY_GID,
    PRODUCT_TAGS,
    METAFIELD_NAMESPACE,
    METAFIELD_NAMESPACE_BOEING,
    TRACE_ALLOWED_DOMAINS,
    UOM_MAPPING,
    CERT_MAPPING,
    METAFIELD_DEFINITIONS,
)
from app.core.constants.sync import (
    MIN_PRODUCTS_FOR_ACTIVE_SLOT,
    MAX_SKUS_PER_API_CALL,
    STUCK_THRESHOLD_MINUTES,
    EOD_STUCK_THRESHOLD_MINUTES,
)

__all__ = [
    "pricing",
    "publishing",
    "extraction",
    "sync",
    "MARKUP_FACTOR",
    "DEFAULT_CURRENCY",
    "DEFAULT_CERTIFICATE",
    "DEFAULT_CONDITION",
    "FALLBACK_IMAGE_URL",
    "DEFAULT_SUPPLIER",
    "DEFAULT_VENDOR",
    "SYSTEM_USER_ID",
    "BOEING_BATCH_SIZE",
    "PRODUCT_CATEGORY_GID",
    "PRODUCT_TAGS",
    "METAFIELD_NAMESPACE",
    "METAFIELD_NAMESPACE_BOEING",
    "TRACE_ALLOWED_DOMAINS",
    "UOM_MAPPING",
    "CERT_MAPPING",
    "METAFIELD_DEFINITIONS",
    "MIN_PRODUCTS_FOR_ACTIVE_SLOT",
    "MAX_SKUS_PER_API_CALL",
    "STUCK_THRESHOLD_MINUTES",
    "EOD_STUCK_THRESHOLD_MINUTES",
]
