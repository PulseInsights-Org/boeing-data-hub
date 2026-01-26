import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.clients.boeing_client import BoeingClient
from app.clients.shopify_client import ShopifyClient
from app.core.config import settings
from app.db.supabase_store import SupabaseStore
from app.routes.auth import router as auth_router
from app.routes.boeing import build_boeing_router
from app.routes.shopify import build_shopify_router
from app.routes.zap import build_zap_router
from app.routes.bulk import router as bulk_router
from app.services.boeing_service import BoeingService
from app.services.shopify_service import ShopifyService
from app.services.zap_service import ZapService

app = FastAPI(title="Boeing Data Hub Backend")
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = SupabaseStore(settings)
boeing_client = BoeingClient(settings)
shopify_client = ShopifyClient(settings)

boeing_service = BoeingService(boeing_client, store)
shopify_service = ShopifyService(shopify_client, store)
zap_service = ZapService(shopify_client, boeing_service, store)

app.include_router(auth_router)
app.include_router(build_boeing_router(boeing_service))
app.include_router(build_shopify_router(shopify_service))
app.include_router(build_zap_router(zap_service))
app.include_router(bulk_router)


@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "healthy"}

