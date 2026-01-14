from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware

from .boeing_client import search_products
from .shopify_client import publish_product, update_product, find_product_by_sku
from .zap_webhook import router as zap_router

app = FastAPI(title="Boeing Data Hub Backend")

# Allow frontend dev server (Vite) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in real production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(zap_router)


@app.get("/api/boeing/product-search")
async def boeing_product_search(query: str = Query(..., min_length=1)):
    """Proxy endpoint: frontend -> FastAPI -> Boeing product-search API."""
    try:
        products = await search_products(query)
        return products
    except HTTPException:
        # re-raise HTTP exceptions from client layer
        raise
    except Exception as exc:  # pragma: no cover - generic guard
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/shopify/publish")
async def shopify_publish(product: dict = Body(...)):
    """Publish a product to Shopify using normalized product payload from frontend."""
    try:
        return await publish_product(product)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.put("/api/shopify/products/{shopify_product_id}")
async def shopify_update(shopify_product_id: str, product: dict = Body(...)):
    """Update an existing Shopify product."""
    try:
        return await update_product(shopify_product_id, product)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/shopify/check")
async def shopify_check(sku: str = Query(...)):
    """Check if a product with given SKU exists in Shopify."""
    try:
        shopify_product_id = await find_product_by_sku(sku)
        return {"shopifyProductId": shopify_product_id}
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


## Deprecated: separate pricing endpoint removed; pricing now comes from Boeing API response.
