import { useState, useCallback, useEffect, useRef } from 'react';
import { RealtimeChannel } from '@supabase/supabase-js';
import {
  fetchPublishedProducts,
  subscribeToProducts,
  unsubscribe,
} from '@/services/realtimeService';

export interface PublishedProduct {
  id: string;
  sku: string;
  title: string;
  body_html: string;
  vendor: string;
  price: number | null;
  cost_per_item: number | null;
  currency: string;
  inventory_quantity: number | null;
  weight: number | null;
  weight_unit: string;
  country_of_origin: string | null;
  dim_length: number | null;
  dim_width: number | null;
  dim_height: number | null;
  dim_uom: string;
  shopify_product_id: string | null;
  image_url: string | null;
  created_at: string;
  updated_at: string;
}

interface UsePublishedProductsReturn {
  products: PublishedProduct[];
  total: number;
  isLoading: boolean;
  error: string | null;
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  refresh: () => Promise<void>;
  loadMore: () => Promise<void>;
  hasMore: boolean;
}

const PAGE_SIZE = 50;

export function usePublishedProducts(): UsePublishedProductsReturn {
  const [products, setProducts] = useState<PublishedProduct[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [offset, setOffset] = useState(0);

  // Realtime subscription reference
  const channelRef = useRef<RealtimeChannel | null>(null);

  // Handle product insert from realtime
  const handleProductInsert = useCallback((product: any) => {
    setProducts(prev => {
      // Check if product already exists
      const exists = prev.some(p => p.id === product.id);
      if (exists) return prev;
      // Add to beginning of list
      return [product as PublishedProduct, ...prev];
    });
    setTotal(prev => prev + 1);
  }, []);

  // Handle product update from realtime
  const handleProductUpdate = useCallback((product: any) => {
    setProducts(prev => {
      const index = prev.findIndex(p => p.id === product.id);
      if (index >= 0) {
        const updated = [...prev];
        updated[index] = product as PublishedProduct;
        return updated;
      }
      return prev;
    });
  }, []);

  // Handle product delete from realtime
  const handleProductDelete = useCallback((oldProduct: { id: string }) => {
    setProducts(prev => prev.filter(p => p.id !== oldProduct.id));
    setTotal(prev => Math.max(0, prev - 1));
  }, []);

  // Set up Supabase Realtime subscription
  useEffect(() => {
    console.log('[usePublishedProducts] Setting up Supabase Realtime subscription');

    channelRef.current = subscribeToProducts(
      handleProductInsert,
      handleProductUpdate,
      handleProductDelete
    );

    return () => {
      if (channelRef.current) {
        console.log('[usePublishedProducts] Cleaning up Realtime subscription');
        unsubscribe(channelRef.current);
        channelRef.current = null;
      }
    };
  }, [handleProductInsert, handleProductUpdate, handleProductDelete]);

  // Fetch products
  const fetchProducts = useCallback(async (reset: boolean = false) => {
    setIsLoading(true);
    setError(null);

    try {
      const currentOffset = reset ? 0 : offset;
      const response = await fetchPublishedProducts(PAGE_SIZE, currentOffset, searchQuery);

      if (reset) {
        setProducts(response.products);
        setOffset(PAGE_SIZE);
      } else {
        setProducts(prev => [...prev, ...response.products]);
        setOffset(prev => prev + PAGE_SIZE);
      }
      setTotal(response.total);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to fetch products';
      setError(errorMessage);
      console.error('[usePublishedProducts] Error:', err);
    } finally {
      setIsLoading(false);
    }
  }, [offset, searchQuery]);

  // Refresh products (reset pagination)
  const refresh = useCallback(async () => {
    setOffset(0);
    await fetchProducts(true);
  }, [fetchProducts]);

  // Load more products
  const loadMore = useCallback(async () => {
    if (!isLoading && products.length < total) {
      await fetchProducts(false);
    }
  }, [fetchProducts, isLoading, products.length, total]);

  // Load initial products on mount
  useEffect(() => {
    fetchProducts(true);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Refetch when search query changes (with debounce)
  useEffect(() => {
    const timeoutId = setTimeout(() => {
      setOffset(0);
      fetchProducts(true);
    }, 300);

    return () => clearTimeout(timeoutId);
  }, [searchQuery]); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    products,
    total,
    isLoading,
    error,
    searchQuery,
    setSearchQuery,
    refresh,
    loadMore,
    hasMore: products.length < total,
  };
}
