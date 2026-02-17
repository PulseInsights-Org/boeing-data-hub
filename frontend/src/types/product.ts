export type ProductStatus = 'fetched' | 'enriched' | 'normalized' | 'published' | 'blocked' | 'failed';

export interface LocationAvailability {
  location: string | null;
  avail_quantity: number | null;
}

export interface BoeingProduct {
  id: string;
  name: string;
  description: string;
  partNumber: string;
  manufacturer: string;
  distrSrc: string;
  pnAUrl: string;
  length: number | null;
  width: number | null;
  height: number | null;
  dimensionUom: string;
  weight: number | null;
  weightUnit: string;
  rawBoeingData: Record<string, unknown>;
}

export interface EnrichedProduct extends BoeingProduct {
  price: number | null;
  inventory: number | null;
  availability: string | null;
  currency?: string | null;
}

export interface NormalizedProduct extends EnrichedProduct {
  status: ProductStatus;
  title: string;
  lastModified: string;
  // Additional Boeing fields
  aviall_part_number?: string | null;
  base_uom?: string | null;
  country_of_origin?: string | null;
  dim?: string | null;
  dim_uom?: string | null;
  eccn?: string | null;
  faa_approval_code?: string | null;
  hazmat_code?: string | null;
  in_stock?: boolean | null;
  list_price?: number | null;
  net_price?: number | null;
  location_availabilities?: LocationAvailability[] | null;
  product_image?: string | null;
  quantity?: number | null;
  schedule_b_code?: string | null;
  supplier_name?: string | null;
  thumbnail_image?: string | null;
  sku?: string | null;
  vendor?: string | null;
  cost_per_item?: number | null;
  inventory_quantity?: number | null;
  inventory_status?: string | null;
  cert?: string | null;
  user_id?: string | null;
  // Backend field name variants
  weight_uom?: string | null;
  dim_length?: number | null;
  dim_width?: number | null;
  dim_height?: number | null;
}

export interface ProductSearchParams {
  query: string;
}

export interface PricingResponse {
  partNumber: string;
  price: number;
  inventory: number;
  availability: 'in_stock' | 'low_stock' | 'out_of_stock';
}

export interface ShopifyPublishResponse {
  success: boolean;
  shopifyProductId?: string;
  error?: string;
  batch_id?: string;
  message?: string;
}

// ============================================
// Bulk Operations Types
// ============================================

export type BatchStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';
export type BatchType = 'extract' | 'normalize' | 'publish';

export interface FailedItem {
  part_number: string;
  error: string;
  stage?: string;
  timestamp?: string;
}

export interface BatchStatusResponse {
  id: string;
  batch_type: BatchType;
  status: BatchStatus;
  total_items: number;
  extracted_count: number;
  normalized_count: number;
  published_count: number;
  failed_count: number;
  progress_percent: number;
  failed_items?: FailedItem[];
  skipped_count: number;
  skipped_part_numbers?: string[];
  part_numbers?: string[];  // Original part numbers from search/extraction
  publish_part_numbers?: string[];  // Part numbers selected for publishing (subset of part_numbers)
  error_message?: string;
  idempotency_key?: string;
  created_at: string;
  updated_at: string;
  completed_at?: string;
}

export interface BulkOperationResponse {
  batch_id: string;
  total_items: number;
  status: string;
  message: string;
  idempotency_key?: string;
}

export interface BulkSearchRequest {
  part_numbers?: string[];
  part_numbers_text?: string;
  idempotency_key?: string;
}

export interface BulkPublishRequest {
  part_numbers?: string[];
  part_numbers_text?: string;
  idempotency_key?: string;
  batch_id?: string;  // If provided, uses existing batch instead of creating new one
}

export interface BatchListResponse {
  batches: BatchStatusResponse[];
  total: number;
}
