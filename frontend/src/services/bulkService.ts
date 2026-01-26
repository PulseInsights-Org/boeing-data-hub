/**
 * Bulk Operations API Service
 *
 * This service handles communication with the backend bulk operations API.
 * Supports:
 * - Starting bulk search operations
 * - Starting bulk publish operations
 * - Polling batch status
 * - Cancelling batches
 */

import {
  BulkSearchRequest,
  BulkPublishRequest,
  BulkOperationResponse,
  BatchStatusResponse,
  BatchListResponse,
} from '@/types/product';
import { getAuthHeaders } from '@/services/authService';

// Base URL for backend API (FastAPI). Configure via Vite env.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

/**
 * Start a bulk search operation.
 * Returns immediately with a batch_id for progress tracking.
 */
export const startBulkSearch = async (
  request: BulkSearchRequest
): Promise<BulkOperationResponse> => {
  const url = new URL('/api/bulk-search', API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Bulk search failed: ${response.status}`);
  }

  return response.json();
};

/**
 * Start a bulk publish operation.
 * Returns immediately with a batch_id for progress tracking.
 */
export const startBulkPublish = async (
  request: BulkPublishRequest
): Promise<BulkOperationResponse> => {
  const url = new URL('/api/bulk-publish', API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Bulk publish failed: ${response.status}`);
  }

  return response.json();
};

/**
 * Get the status of a specific batch.
 */
export const getBatchStatus = async (batchId: string): Promise<BatchStatusResponse> => {
  const url = new URL(`/api/batches/${batchId}`, API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to get batch status: ${response.status}`);
  }

  return response.json();
};

/**
 * List all batches with optional filtering.
 */
export const listBatches = async (
  limit: number = 50,
  offset: number = 0,
  status?: string
): Promise<BatchListResponse> => {
  const url = new URL('/api/batches', API_BASE_URL || window.location.origin);
  url.searchParams.set('limit', limit.toString());
  url.searchParams.set('offset', offset.toString());
  if (status) {
    url.searchParams.set('status', status);
  }

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to list batches: ${response.status}`);
  }

  return response.json();
};

/**
 * Cancel a batch operation.
 */
export const cancelBatch = async (batchId: string): Promise<{ message: string; batch_id: string }> => {
  const url = new URL(`/api/batches/${batchId}`, API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'DELETE',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to cancel batch: ${response.status}`);
  }

  return response.json();
};

/**
 * Parse part numbers from text input.
 * Supports comma, semicolon, or newline separated values.
 */
export const parsePartNumbers = (text: string): string[] => {
  return text
    .split(/[,;\n\r]+/)
    .map(pn => pn.trim())
    .filter(pn => pn.length > 0);
};

/**
 * Response type for staging products endpoint.
 */
export interface StagingProductsResponse {
  products: StagingProduct[];
  total: number;
  limit: number;
  offset: number;
}

/**
 * Product from product_staging table.
 */
export interface StagingProduct {
  id: string;
  sku: string;
  title: string;
  body_html: string;
  vendor: string;
  price: number | null;
  cost_per_item: number | null;
  list_price: number | null;
  net_price: number | null;
  currency: string | null;
  inventory_quantity: number | null;
  inventory_status: string | null;
  location_summary: string | null;
  weight: number | null;
  weight_unit: string | null;
  country_of_origin: string | null;
  dim_length: number | null;
  dim_width: number | null;
  dim_height: number | null;
  dim_uom: string | null;
  base_uom: string | null;
  hazmat_code: string | null;
  faa_approval_code: string | null;
  eccn: string | null;
  schedule_b_code: string | null;
  supplier_name: string | null;
  boeing_name: string | null;
  boeing_description: string | null;
  boeing_image_url: string | null;
  boeing_thumbnail_url: string | null;
  image_url: string | null;
  image_path: string | null;
  condition: string | null;
  pma: boolean | null;
  estimated_lead_time_days: number | null;
  trace: string | null;
  expiration_date: string | null;
  notes: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  user_id: string | null;
}

/**
 * Fetch products from product_staging table.
 * These are normalized products from bulk search operations.
 *
 * @param limit - Maximum number of products to return
 * @param offset - Number of products to skip
 * @param status - Optional status filter (fetched, enriched, published)
 * @param batchId - Optional batch ID to filter products by the batch they were created in
 */
export const getStagingProducts = async (
  limit: number = 100,
  offset: number = 0,
  status?: string,
  batchId?: string
): Promise<StagingProductsResponse> => {
  const url = new URL('/api/products/staging', API_BASE_URL || window.location.origin);
  url.searchParams.set('limit', limit.toString());
  url.searchParams.set('offset', offset.toString());
  if (status) {
    url.searchParams.set('status', status);
  }
  if (batchId) {
    url.searchParams.set('batch_id', batchId);
  }

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch staging products: ${response.status}`);
  }

  return response.json();
};

/**
 * Response type for raw Boeing data endpoint.
 */
export interface RawBoeingDataResponse {
  raw_data: Record<string, unknown> | null;
  search_query?: string;
  fetched_at?: string;
  message?: string;
}

/**
 * Fetch raw Boeing API data for a specific part number.
 * This queries the boeing_raw_data table for historical API responses.
 */
export const getRawBoeingData = async (partNumber: string): Promise<RawBoeingDataResponse> => {
  const url = new URL(`/api/products/raw-data/${encodeURIComponent(partNumber)}`, API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch raw data: ${response.status}`);
  }

  return response.json();
};
