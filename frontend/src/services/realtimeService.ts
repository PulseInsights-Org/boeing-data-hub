/**
 * Supabase Realtime Service
 *
 * Provides real-time subscriptions for:
 * - Batch status updates (batches table)
 * - Product staging updates (product_staging table)
 * - Published products updates (product table)
 */

import { createClient, SupabaseClient, RealtimeChannel } from '@supabase/supabase-js';
import { BatchStatusResponse } from '@/types/product';

// Supabase configuration from Vite env
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  throw new Error('Supabase env vars missing: VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY');
}

// Create a single Supabase client instance for realtime
const supabase: SupabaseClient = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  realtime: {
    params: {
      eventsPerSecond: 10,
    },
  },
});

export type BatchChangeCallback = (batch: BatchStatusResponse) => void;
export type ProductChangeCallback = (product: any) => void;

/**
 * Subscribe to real-time batch updates
 */
export function subscribeToBatches(
  onInsert: BatchChangeCallback,
  onUpdate: BatchChangeCallback,
  onDelete?: (oldBatch: { id: string }) => void
): RealtimeChannel {
  const channel = supabase
    .channel('batches-changes')
    .on(
      'postgres_changes',
      {
        event: 'INSERT',
        schema: 'public',
        table: 'batches',
      },
      (payload) => {
        console.log('[Realtime] Batch inserted:', payload.new);
        onInsert(transformBatchRecord(payload.new));
      }
    )
    .on(
      'postgres_changes',
      {
        event: 'UPDATE',
        schema: 'public',
        table: 'batches',
      },
      (payload) => {
        console.log('[Realtime] Batch updated:', payload.new);
        onUpdate(transformBatchRecord(payload.new));
      }
    )
    .on(
      'postgres_changes',
      {
        event: 'DELETE',
        schema: 'public',
        table: 'batches',
      },
      (payload) => {
        console.log('[Realtime] Batch deleted:', payload.old);
        if (onDelete) {
          onDelete({ id: payload.old.id });
        }
      }
    )
    .subscribe((status) => {
      console.log('[Realtime] Batches subscription status:', status);
    });

  return channel;
}

/**
 * Subscribe to real-time product_staging updates for a specific batch
 */
export function subscribeToStagingProducts(
  batchId: string,
  onUpdate: ProductChangeCallback
): RealtimeChannel {
  const channel = supabase
    .channel(`staging-${batchId}`)
    .on(
      'postgres_changes',
      {
        event: 'UPDATE',
        schema: 'public',
        table: 'product_staging',
        filter: `batch_id=eq.${batchId}`,
      },
      (payload) => {
        console.log('[Realtime] Staging product updated:', payload.new);
        onUpdate(payload.new);
      }
    )
    .subscribe((status) => {
      console.log(`[Realtime] Staging products (${batchId}) subscription status:`, status);
    });

  return channel;
}

/**
 * Subscribe to all product_staging updates (for any status changes)
 */
export function subscribeToAllStagingUpdates(
  onUpdate: ProductChangeCallback
): RealtimeChannel {
  const channel = supabase
    .channel('all-staging-updates')
    .on(
      'postgres_changes',
      {
        event: 'UPDATE',
        schema: 'public',
        table: 'product_staging',
      },
      (payload) => {
        console.log('[Realtime] Staging product updated:', payload.new);
        onUpdate(payload.new);
      }
    )
    .subscribe((status) => {
      console.log('[Realtime] All staging updates subscription status:', status);
    });

  return channel;
}

/**
 * Subscribe to real-time products table updates (published products)
 */
export function subscribeToProducts(
  onInsert: ProductChangeCallback,
  onUpdate: ProductChangeCallback,
  onDelete?: (oldProduct: { id: string }) => void
): RealtimeChannel {
  const channel = supabase
    .channel('products-changes')
    .on(
      'postgres_changes',
      {
        event: 'INSERT',
        schema: 'public',
        table: 'product',
      },
      (payload) => {
        console.log('[Realtime] Product inserted:', payload.new);
        onInsert(payload.new);
      }
    )
    .on(
      'postgres_changes',
      {
        event: 'UPDATE',
        schema: 'public',
        table: 'product',
      },
      (payload) => {
        console.log('[Realtime] Product updated:', payload.new);
        onUpdate(payload.new);
      }
    )
    .on(
      'postgres_changes',
      {
        event: 'DELETE',
        schema: 'public',
        table: 'product',
      },
      (payload) => {
        console.log('[Realtime] Product deleted:', payload.old);
        if (onDelete) {
          onDelete({ id: payload.old.id });
        }
      }
    )
    .subscribe((status) => {
      console.log('[Realtime] Products subscription status:', status);
    });

  return channel;
}

/**
 * Unsubscribe from a channel
 */
export function unsubscribe(channel: RealtimeChannel): void {
  supabase.removeChannel(channel);
}

/**
 * Transform a raw batch record to BatchStatusResponse format
 *
 * Progress calculation matches backend _calculate_progress:
 * - 'search': (normalized_count + failed_count) / total
 * - 'normalized': 100% (search is complete)
 * - 'publishing' or 'publish': (published_count + failed_count) / total
 */
function transformBatchRecord(record: any): BatchStatusResponse {
  const total = record.total_items || 0;

  // Calculate progress based on batch type (matches backend logic)
  let progressPercent = 0;
  if (total > 0) {
    const batchType = record.batch_type;

    if (batchType === 'search') {
      // Search stage: progress based on normalization
      const completed = (record.normalized_count || 0) + (record.failed_count || 0);
      progressPercent = (completed / total) * 100;
    } else if (batchType === 'normalized') {
      // Normalized stage: search is complete, show 100%
      progressPercent = 100;
    } else if (batchType === 'publishing' || batchType === 'publish') {
      // Publishing stage: progress based on published items
      // Use publish_part_numbers length as total if available, otherwise use total_items
      const publishTotal = record.publish_part_numbers?.length || total;
      const completed = (record.published_count || 0) + (record.failed_count || 0);
      progressPercent = publishTotal > 0 ? (completed / publishTotal) * 100 : 0;
    }
  }

  return {
    id: record.id,
    batch_type: record.batch_type,
    status: record.status,
    total_items: record.total_items || 0,
    extracted_count: record.extracted_count || 0,
    normalized_count: record.normalized_count || 0,
    published_count: record.published_count || 0,
    failed_count: record.failed_count || 0,
    progress_percent: Math.min(progressPercent, 100), // Cap at 100%
    failed_items: record.failed_items || [],
    part_numbers: record.part_numbers || [],
    publish_part_numbers: record.publish_part_numbers || [],
    error_message: record.error_message,
    idempotency_key: record.idempotency_key,
    created_at: record.created_at,
    updated_at: record.updated_at,
    completed_at: record.completed_at,
  };
}

/**
 * Fetch published products from the product table
 * Uses specific columns to reduce payload size and prevent worker timeouts
 */
export async function fetchPublishedProducts(
  limit: number = 100,
  offset: number = 0,
  searchQuery?: string
): Promise<{ products: any[]; total: number }> {
  // Select only needed columns to avoid large payloads that cause Cloudflare worker timeouts
  const columns = [
    'id', 'sku', 'title', 'body_html', 'vendor', 'price', 'cost_per_item', 'currency',
    'inventory_quantity', 'weight', 'weight_unit', 'country_of_origin',
    'dim_length', 'dim_width', 'dim_height', 'dim_uom',
    'shopify_product_id', 'image_url', 'created_at', 'updated_at'
  ].join(',');

  try {
    let query = supabase
      .from('product')
      .select(columns, { count: 'exact' });

    if (searchQuery && searchQuery.trim()) {
      // Search by sku (part number) using ilike for case-insensitive partial match
      query = query.ilike('sku', `%${searchQuery.trim()}%`);
    }

    query = query.range(offset, offset + limit - 1);

    const { data, error, count } = await query;

    if (error) {
      console.error('[Realtime] Supabase error:', error.message, error.details, error.hint);
      throw new Error(error.message || 'Failed to fetch products');
    }

    console.log('[Realtime] Fetched products:', data?.length, 'total:', count);

    return {
      products: data || [],
      total: count || 0,
    };
  } catch (err) {
    console.error('[Realtime] Error fetching published products:', err);
    throw err;
  }
}

export { supabase };
