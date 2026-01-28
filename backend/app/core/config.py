import json
import os
from typing import Optional
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel


load_dotenv()


class Settings(BaseModel):
    # AWS Cognito settings (for SSO authentication from Aviation Gateway)
    cognito_region: str = os.getenv("COGNITO_REGION", "us-east-1")
    cognito_user_pool_id: Optional[str] = os.getenv("COGNITO_USER_POOL_ID")
    cognito_app_client_id: Optional[str] = os.getenv("COGNITO_APP_CLIENT_ID")

    @property
    def cognito_issuer(self) -> str:
        """Get the Cognito issuer URL."""
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"

    @property
    def cognito_jwks_url(self) -> str:
        """Get the Cognito JWKS URL for token verification."""
        return f"{self.cognito_issuer}/.well-known/jwks.json"

    # Supabase
    supabase_url: str | None = os.getenv("SUPABASE_URL")
    supabase_key: str | None = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    supabase_service_role_key: str | None = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    supabase_storage_bucket: str = os.getenv("SUPABASE_STORAGE_BUCKET", "product-images")

    # Shopify
    shopify_store_domain: str | None = os.getenv("SHOPIFY_STORE_DOMAIN")
    shopify_admin_api_token: str | None = os.getenv("SHOPIFY_ADMIN_API_TOKEN")
    shopify_api_version: str = os.getenv("SHOPIFY_API_VERSION", "2024-10")
    shopify_location_map: dict[str, str] = json.loads(os.getenv("SHOPIFY_LOCATION_MAP", "{}"))
    # Map Boeing location names to 3-char inventory location codes for Shopify metafield
    # Example: {"Dallas Central": "1D1", "Chicago Warehouse": "CHI"}
    shopify_inventory_location_codes: dict[str, str] = json.loads(os.getenv("SHOPIFY_INVENTORY_LOCATION_CODES", "{}"))

    # Boeing
    boeing_oauth_token_url: str = os.getenv(
        "BOEING_OAUTH_TOKEN_URL",
        "https://api.developer.boeingservices.com/oauth2/v2.0/token",
    )
    boeing_client_id: str | None = os.getenv("BOEING_CLIENT_ID")
    boeing_client_secret: str | None = os.getenv("BOEING_CLIENT_SECRET")
    boeing_scope: str = os.getenv("BOEING_SCOPE", "api://helixapis.com/.default")
    boeing_pna_oauth_url: str = os.getenv(
        "BOEING_PNA_OAUTH_URL",
        "https://api.developer.boeingservices.com/boeing-part-price-availability/token/v1/oauth",
    )
    boeing_pna_price_url: str = os.getenv(
        "BOEING_PNA_PRICE_URL",
        "https://api.developer.boeingservices.com/boeing-part-price-availability/price-availability/v1/wtoken",
    )
    boeing_username: str | None = os.getenv("BOEING_USERNAME")
    boeing_password: str | None = os.getenv("BOEING_PASSWORD")

    # Redis
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # Celery
    celery_broker_url: str = os.getenv("CELERY_BROKER_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    celery_result_backend: str = os.getenv("CELERY_RESULT_BACKEND", os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    # Worker concurrency
    celery_extraction_concurrency: int = int(os.getenv("CELERY_EXTRACTION_CONCURRENCY", "2"))
    celery_normalization_concurrency: int = int(os.getenv("CELERY_NORMALIZATION_CONCURRENCY", "4"))
    celery_shopify_concurrency: int = int(os.getenv("CELERY_SHOPIFY_CONCURRENCY", "1"))

    # Batch processing settings
    boeing_batch_size: int = int(os.getenv("BOEING_BATCH_SIZE", "10"))
    max_bulk_search_size: int = int(os.getenv("MAX_BULK_SEARCH_SIZE", "50000"))
    max_bulk_publish_size: int = int(os.getenv("MAX_BULK_PUBLISH_SIZE", "10000"))

    # Rate limits
    boeing_api_rate_limit: str = os.getenv("BOEING_API_RATE_LIMIT", "20/m")
    shopify_api_rate_limit: str = os.getenv("SHOPIFY_API_RATE_LIMIT", "30/m")


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = Settings()
