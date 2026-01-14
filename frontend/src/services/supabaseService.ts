/**
 * Supabase Data Persistence Service
 * 
 * This service handles all database operations for storing:
 * - Raw Boeing product data (for auditing/reference)
 * - Normalized product data (for editing/publishing)
 * - Product status and history
 */

import { createClient, SupabaseClient } from '@supabase/supabase-js';
import { BoeingProduct, NormalizedProduct } from '@/types/product';

// Supabase configuration from Vite env (frontend-safe keys)
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  // Fail fast in development if env is misconfigured
  throw new Error('Supabase env vars missing: VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY');
}

const supabase: SupabaseClient = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// Table names aligned with agreed schema
const SUPABASE_TABLE_RAW = 'boeing_raw_data';
const SUPABASE_TABLE_STAGING = 'product_staging';

/**
 * Store raw Boeing product data for auditing purposes
 * Preserves original API response structure
 */
export const storeRawBoeingData = async (products: BoeingProduct[]): Promise<void> => {
  console.warn('[SupabaseService] storeRawBoeingData is deprecated; raw data is now stored on the backend');
};

/**
 * Store or update normalized product data
 * Called after editing or enriching products
 */
export const saveNormalizedProduct = async (product: NormalizedProduct): Promise<void> => {
  console.log(`[SupabaseService] Saving normalized product (Shopify-friendly): ${product.partNumber}`);

  const now = new Date().toISOString();

  const sku = product.partNumber;
  const title = product.title || product.name;
  const bodyHtml = product.description;
  const vendor = product.manufacturer || product.distrSrc || '';

  const upsertPayload = {
    id: sku,
    sku,
    title,
    body_html: bodyHtml,
    vendor,
    price: product.price,
    currency: product.currency ?? 'USD',
    inventory_quantity: product.inventory,
    inventory_status: product.availability,
    weight: product.weight,
    weight_unit: product.weightUnit,
    country_of_origin: (product.rawBoeingData as any)?.countryOfOrigin ?? null,
    dim_length: product.length,
    dim_width: product.width,
    dim_height: product.height,
    dim_uom: product.dimensionUom,
    status: product.status,
    updated_at: now,
  };

  const { error } = await supabase.from(SUPABASE_TABLE_STAGING).upsert(upsertPayload, {
    onConflict: 'id',
  });

  if (error) {
    console.error('[SupabaseService] Error saving normalized product', error);
    throw error;
  }
};

/**
 * Fetch all normalized products from database
 */
export const fetchNormalizedProducts = async (): Promise<NormalizedProduct[]> => {
  console.log('[SupabaseService] Fetching normalized products');

  const { data, error } = await supabase
    .from(SUPABASE_TABLE_STAGING)
    .select('*')
    .order('updated_at', { ascending: false });

  if (error) {
    console.error('[SupabaseService] Error fetching normalized products', error);
    throw error;
  }

  if (!data) return [];

  return data.map((row: any) => {
    const lastModified = row.updated_at || row.created_at || new Date().toISOString();

    const normalized: NormalizedProduct = {
      id: row.id,
      name: row.title || row.sku,
      description: row.body_html ?? '',
      partNumber: row.sku,
      manufacturer: row.vendor ?? '',
      distrSrc: row.vendor ?? '',
      pnAUrl: '',
      length: row.dim_length,
      width: row.dim_width,
      height: row.dim_height,
      dimensionUom: row.dim_uom ?? '',
      weight: row.weight,
      weightUnit: row.weight_unit ?? '',
      rawBoeingData: {},
      price: row.price ?? null,
      inventory: row.inventory_quantity ?? null,
      availability: row.inventory_status ?? null,
      currency: row.currency ?? null,
      status: (row.status as any) ?? 'fetched',
      title: row.title || row.sku,
      lastModified,
    };

    return normalized;
  });
};

/**
 * Update product status after publishing
 */
export const updateProductStatus = async (
  productId: string, 
  status: 'fetched' | 'enriched' | 'published'
): Promise<void> => {
  console.log(`[SupabaseService] Updating product ${productId} status to: ${status}`);

  const now = new Date().toISOString();

  const { error } = await supabase
    .from(SUPABASE_TABLE_STAGING)
    .update({ status, updated_at: now })
    .eq('id', productId);

  if (error) {
    console.error('[SupabaseService] Error updating product status', error);
    throw error;
  }
};
