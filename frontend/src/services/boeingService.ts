/**
 * Boeing Commerce Connect API Service
 * 
 * This service handles communication with Boeing Product APIs.
 * In production, this would:
 * 1. Generate JWT assertion for authentication
 * 2. Exchange assertion for access token
 * 3. Make authenticated API calls to Boeing endpoints
 */

import { BoeingProduct, ProductSearchParams } from '@/types/product';
import { getAuthHeaders } from '@/services/authService';

// Base URL for backend API (FastAPI). Configure via Vite env.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

/**
 * Search Boeing products by query term via backend API.
 * Backend is responsible for JWT generation, token exchange,
 * and calling the Boeing product search endpoint.
 */
export const searchProducts = async (params: ProductSearchParams): Promise<BoeingProduct[]> => {
  const url = new URL('/api/v1/extraction/search', API_BASE_URL || window.location.origin);
  url.searchParams.set('query', params.query);

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  });

  if (!response.ok) {
    const errorText = await response.text().catch(() => '');
    throw new Error(`Boeing product search failed: ${response.status} ${errorText}`);
  }

  const data = await response.json();
  return data as BoeingProduct[];
};

/**
 * Get single product details by part number
 */
export const getProductByPartNumber = async (partNumber: string): Promise<BoeingProduct | null> => {
  console.log(`[BoeingService] Fetching product: ${partNumber}`);
  // Optional: implement backend endpoint for single-product lookup if needed
  return null;
};
