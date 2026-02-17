"""
Sync constants — bucket limits, currency defaults, failure thresholds.

Sync scheduler constants.
Version: 1.0.0
"""

DEFAULT_CURRENCY: str = "USD"

# Minimum products in a slot before it's considered "active" (vs "filling")
MIN_PRODUCTS_FOR_ACTIVE_SLOT: int = 10

# Max SKUs per Boeing API call
MAX_SKUS_PER_API_CALL: int = 10

# Minutes before a "syncing" product is considered stuck
STUCK_THRESHOLD_MINUTES: int = 30

# End-of-day cleanup stuck threshold (more generous)
EOD_STUCK_THRESHOLD_MINUTES: int = 60
