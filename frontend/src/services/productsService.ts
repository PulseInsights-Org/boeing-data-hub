/**
 * Products API Service
 *
 * Handles fetching published products from the backend API.
 * Routes through the backend to avoid direct Supabase connection issues.
 */

import { getAuthHeaders } from '@/services/authService';

// Base URL for backend API
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

export interface PublishedProduct {
  id: string;
  sku: string;
  title: string | null;
  body_html: string | null;
  vendor: string | null;
  price: number | null;
  cost_per_item: number | null;
  currency: string | null;
  inventory_quantity: number | null;
  weight: number | null;
  weight_unit: string | null;
  country_of_origin: string | null;
  dim_length: number | null;
  dim_width: number | null;
  dim_height: number | null;
  dim_uom: string | null;
  shopify_product_id: string | null;
  image_url: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface PublishedProductsResponse {
  products: PublishedProduct[];
  total: number;
  shopify_store_domain: string | null;
}

/**
 * Fetch published products from the backend API.
 * Supports pagination and search by SKU.
 */
export async function fetchPublishedProducts(
  limit: number = 50,
  offset: number = 0,
  search?: string
): Promise<PublishedProductsResponse> {
  const url = new URL('/api/products/published', API_BASE_URL || window.location.origin);

  url.searchParams.set('limit', limit.toString());
  url.searchParams.set('offset', offset.toString());

  if (search && search.trim()) {
    url.searchParams.set('search', search.trim());
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
    throw new Error(errorData.detail || `Failed to fetch products: ${response.status}`);
  }

  return response.json();
}

/**
 * Fetch a single published product by ID.
 */
export async function fetchPublishedProduct(productId: string): Promise<PublishedProduct> {
  const url = new URL(`/api/products/published/${productId}`, API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || `Failed to fetch product: ${response.status}`);
  }

  return response.json();
}
