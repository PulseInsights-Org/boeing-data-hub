"""
Pricing constants — markup factor, fallback image, default cert/condition.

Pricing and markup constants.

Every pricing rule lives here. When markup changes, update ONE file.
Version: 1.0.0
"""

# Storefront price = base_cost * MARKUP_FACTOR
MARKUP_FACTOR: float = 1.1

DEFAULT_CURRENCY: str = "USD"

# Default product attributes for Shopify
DEFAULT_CERTIFICATE: str = "FAA 8130-3"
DEFAULT_CONDITION: str = "NE"

# Fallback image when Boeing image is unavailable
FALLBACK_IMAGE_URL: str = (
    "https://placehold.co/800x600/e8e8e8/666666/png"
    "?text=Image+Not+Available&font=roboto"
)
