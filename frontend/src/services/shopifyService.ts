/**
 * Shopify Publishing Service
 *
 * This service handles publishing normalized products to Shopify.
 * In production, this would:
 * 1. Transform normalized product data to Shopify format
 * 2. Create or update products via Shopify Admin API
 * 3. Handle variant creation, inventory sync, etc.
 */

import { NormalizedProduct, ShopifyPublishResponse } from '@/types/product';
import { getAuthHeaders } from '@/services/authService';

// Backend API base URL (FastAPI) for Shopify operations
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

/**
 * Publish a normalized product to Shopify
 * @param product - The product to publish
 * @param batchId - Optional batch ID to use (continues existing batch pipeline)
 */
export const publishToShopify = async (
  product: NormalizedProduct,
  batchId?: string
): Promise<ShopifyPublishResponse> => {
  const partNumber = product.partNumber || product.sku;
  if (!partNumber) {
    return {
      success: false,
      error: 'Missing part number for Shopify publish.',
    };
  }
  console.log(`[ShopifyService] Publishing product to Shopify: ${partNumber}${batchId ? ` (batch: ${batchId})` : ''}`);

  const url = new URL('/api/v1/publishing/publish', API_BASE_URL || window.location.origin);

  const payload: { part_number: string; batch_id?: string } = { part_number: partNumber };
  if (batchId) {
    payload.batch_id = batchId;
  }

  const response = await fetch(url.toString(), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    console.error('[ShopifyService] Backend publish failed', response.status, text);
    return {
      success: false,
      error: `Backend publish failed: ${response.status} ${text}`,
    };
  }

  const data = (await response.json()) as ShopifyPublishResponse;

  // The publish is now queued via Celery worker - status updates come via realtime subscription
  if (data.batch_id) {
    console.log(`[ShopifyService] Publish queued for ${partNumber}, batch_id=${data.batch_id}`);
  }

  return data;
};

/**
 * Update an existing Shopify product
 */
export const updateShopifyProduct = async (
  shopifyProductId: string,
  product: NormalizedProduct
): Promise<ShopifyPublishResponse> => {
  console.log(`[ShopifyService] Updating Shopify product: ${shopifyProductId}`);

  const url = new URL(`/api/v1/publishing/products/${encodeURIComponent(shopifyProductId)}`, API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'PUT',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
    body: JSON.stringify(product),
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    console.error('[ShopifyService] Backend update failed', response.status, text);
    return {
      success: false,
      error: `Backend update failed: ${response.status} ${text}`,
    };
  }

  const data = (await response.json()) as ShopifyPublishResponse;
  return data;
};

/**
 * Check if product already exists in Shopify by SKU
 */
export const checkProductExists = async (sku: string): Promise<string | null> => {
  console.log(`[ShopifyService] Checking if SKU exists: ${sku}`);

  const url = new URL('/api/v1/publishing/check', API_BASE_URL || window.location.origin);
  url.searchParams.set('sku', sku);

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    console.error('[ShopifyService] Backend check failed', response.status, text);
    return null;
  }

  const data = (await response.json()) as { shopifyProductId: string | null };
  return data.shopifyProductId ?? null;
};
