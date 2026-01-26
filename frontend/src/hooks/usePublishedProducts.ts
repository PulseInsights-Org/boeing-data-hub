import { useState, useCallback, useEffect } from 'react';
import {
  fetchPublishedProducts,
  PublishedProduct,
} from '@/services/productsService';

// Re-export the type for use in components
export type { PublishedProduct } from '@/services/productsService';

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

  // Fetch products from backend API
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
