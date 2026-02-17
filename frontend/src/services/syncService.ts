/**
 * Sync Dashboard API Service
 *
 * Provides API calls for the Auto-Sync dashboard:
 * - Dashboard overview stats
 * - Hourly distribution
 * - Sync history
 * - Failed products
 * - Product sync status
 */

import { getAuthHeaders, handleUnauthorized } from '@/services/authService';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

// ============================================================
// Types
// ============================================================

export interface SyncStatusCounts {
  pending: number;
  syncing: number;
  success: number;
  failed: number;
}

export interface SlotInfo {
  hour: number;
  count: number;
  status: 'active' | 'filling' | 'dormant';
}

export interface SyncDashboardData {
  // Overview stats
  total_products: number;
  active_products: number;
  inactive_products: number;
  success_rate_percent: number;
  high_failure_count: number;

  // Status breakdown
  status_counts: SyncStatusCounts;

  // Current sync info
  current_hour: number;
  current_hour_products: number;
  sync_mode: 'production' | 'testing';
  max_buckets: number;

  // Slot distribution
  slot_distribution: SlotInfo[];
  active_slots: number;
  filling_slots: number;
  dormant_slots: number;
  efficiency_percent: number;

  // Timestamps
  last_updated: string;
}

export interface SyncProduct {
  id: string;
  sku: string;
  user_id: string;
  hour_bucket: number;
  sync_status: 'pending' | 'syncing' | 'success' | 'failed';
  last_sync_at: string | null;
  consecutive_failures: number;
  last_error: string | null;
  last_price: number | null;
  last_quantity: number | null;
  last_inventory_status: string | null;
  last_location_summary: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface SyncProductsResponse {
  products: SyncProduct[];
  total: number;
  limit: number;
  offset: number;
}

export interface SyncHistoryItem {
  sku: string;
  sync_status: string;
  last_sync_at: string | null;
  last_price: number | null;
  last_quantity: number | null;
  last_inventory_status: string | null;
  last_error: string | null;
  hour_bucket: number;
}

export interface SyncHistoryResponse {
  items: SyncHistoryItem[];
  total: number;
}

export interface FailedProduct {
  sku: string;
  consecutive_failures: number;
  last_error: string | null;
  last_sync_at: string | null;
  hour_bucket: number;
  is_active: boolean;
}

export interface FailedProductsResponse {
  products: FailedProduct[];
  total: number;
}

export interface HourlyStats {
  hour: number;
  total: number;
  pending: number;
  syncing: number;
  success: number;
  failed: number;
}

export interface HourlyStatsResponse {
  hours: HourlyStats[];
  current_hour: number;
}

// ============================================================
// API Functions
// ============================================================

/**
 * Fetch complete sync dashboard data
 */
export async function fetchSyncDashboard(): Promise<SyncDashboardData> {
  const url = new URL('/api/v1/sync/dashboard', API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    if (response.status === 401) {
      handleUnauthorized();
      throw new Error('Session expired. Redirecting to login...');
    }
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch dashboard: ${response.status}`);
  }

  return response.json();
}

/**
 * Fetch sync products with filtering
 */
export async function fetchSyncProducts(
  options: {
    limit?: number;
    offset?: number;
    status?: string;
    hour?: number;
    activeOnly?: boolean;
    search?: string;
  } = {}
): Promise<SyncProductsResponse> {
  const url = new URL('/api/v1/sync/products', API_BASE_URL || window.location.origin);

  if (options.limit) url.searchParams.set('limit', options.limit.toString());
  if (options.offset) url.searchParams.set('offset', options.offset.toString());
  if (options.status) url.searchParams.set('status', options.status);
  if (options.hour !== undefined) url.searchParams.set('hour', options.hour.toString());
  if (options.activeOnly !== undefined) url.searchParams.set('active_only', options.activeOnly.toString());
  if (options.search) url.searchParams.set('search', options.search);

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    if (response.status === 401) {
      handleUnauthorized();
      throw new Error('Session expired. Redirecting to login...');
    }
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch products: ${response.status}`);
  }

  return response.json();
}

/**
 * Fetch recent sync history
 */
export async function fetchSyncHistory(
  limit: number = 50,
  hoursBack: number = 24
): Promise<SyncHistoryResponse> {
  const url = new URL('/api/v1/sync/history', API_BASE_URL || window.location.origin);
  url.searchParams.set('limit', limit.toString());
  url.searchParams.set('hours_back', hoursBack.toString());

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    if (response.status === 401) {
      handleUnauthorized();
      throw new Error('Session expired. Redirecting to login...');
    }
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch history: ${response.status}`);
  }

  return response.json();
}

/**
 * Fetch failed products
 */
export async function fetchFailedProducts(
  limit: number = 50,
  includeInactive: boolean = false
): Promise<FailedProductsResponse> {
  const url = new URL('/api/v1/sync/failures', API_BASE_URL || window.location.origin);
  url.searchParams.set('limit', limit.toString());
  url.searchParams.set('include_inactive', includeInactive.toString());

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    if (response.status === 401) {
      handleUnauthorized();
      throw new Error('Session expired. Redirecting to login...');
    }
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch failures: ${response.status}`);
  }

  return response.json();
}

/**
 * Fetch hourly stats breakdown
 */
export async function fetchHourlyStats(): Promise<HourlyStatsResponse> {
  const url = new URL('/api/v1/sync/hourly-stats', API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    if (response.status === 401) {
      handleUnauthorized();
      throw new Error('Session expired. Redirecting to login...');
    }
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch hourly stats: ${response.status}`);
  }

  return response.json();
}

/**
 * Get sync status for a specific product
 */
export async function fetchProductSyncStatus(sku: string): Promise<SyncProduct> {
  const url = new URL(`/api/v1/sync/product/${encodeURIComponent(sku)}`, API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    if (response.status === 401) {
      handleUnauthorized();
      throw new Error('Session expired. Redirecting to login...');
    }
    if (response.status === 404) {
      throw new Error('Product not found in sync schedule');
    }
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch product: ${response.status}`);
  }

  return response.json();
}

/**
 * Reactivate a deactivated product
 */
export async function reactivateProduct(sku: string): Promise<{ message: string; sku: string }> {
  const url = new URL(`/api/v1/sync/product/${encodeURIComponent(sku)}/reactivate`, API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    if (response.status === 401) {
      handleUnauthorized();
      throw new Error('Session expired. Redirecting to login...');
    }
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to reactivate: ${response.status}`);
  }

  return response.json();
}

/**
 * Trigger immediate sync for a product
 */
export async function triggerImmediateSync(sku: string): Promise<{ status: string; sku: string; message: string }> {
  const url = new URL(`/api/v1/sync/trigger/${encodeURIComponent(sku)}`, API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'POST',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    if (response.status === 401) {
      handleUnauthorized();
      throw new Error('Session expired. Redirecting to login...');
    }
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to trigger sync: ${response.status}`);
  }

  return response.json();
}
