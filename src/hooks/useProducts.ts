import { useState, useCallback, useReducer } from 'react';
import { NormalizedProduct, ProductStatus } from '@/types/product';
import { searchProducts } from '@/services/boeingService';
import { getPricingAndInventory } from '@/services/pricingService';
import { storeRawBoeingData, saveNormalizedProduct } from '@/services/supabaseService';
import { publishToShopify } from '@/services/shopifyService';

// Action types for reducer
type ProductAction =
  | { type: 'SET_PRODUCTS'; payload: NormalizedProduct[] }
  | { type: 'ADD_PRODUCTS'; payload: NormalizedProduct[] }
  | { type: 'UPDATE_PRODUCT'; payload: NormalizedProduct }
  | { type: 'SET_STATUS'; payload: { id: string; status: ProductStatus } };

// Reducer for product state management
function productsReducer(state: NormalizedProduct[], action: ProductAction): NormalizedProduct[] {
  switch (action.type) {
    case 'SET_PRODUCTS':
      return action.payload;
    case 'ADD_PRODUCTS':
      return [...state, ...action.payload];
    case 'UPDATE_PRODUCT':
      return state.map(p => 
        p.id === action.payload.id ? action.payload : p
      );
    case 'SET_STATUS':
      return state.map(p => 
        p.id === action.payload.id 
          ? { ...p, status: action.payload.status, lastModified: new Date().toISOString() } 
          : p
      );
    default:
      return state;
  }
}

interface UseProductsReturn {
  products: NormalizedProduct[];
  selectedProduct: NormalizedProduct | null;
  isLoading: boolean;
  error: string | null;
  actionLoading: { [key: string]: boolean };
  selectProduct: (product: NormalizedProduct | null) => void;
  fetchProducts: (query: string) => Promise<void>;
  enrichProduct: (productId: string) => Promise<void>;
  updateProduct: (product: NormalizedProduct) => Promise<void>;
  publishProduct: (productId: string) => Promise<{ success: boolean; error?: string }>;
  clearError: () => void;
}

export function useProducts(): UseProductsReturn {
  const [products, dispatch] = useReducer(productsReducer, []);
  const [selectedProduct, setSelectedProduct] = useState<NormalizedProduct | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<{ [key: string]: boolean }>({});

  const setActionState = useCallback((key: string, loading: boolean) => {
    setActionLoading(prev => ({ ...prev, [key]: loading }));
  }, []);

  const selectProduct = useCallback((product: NormalizedProduct | null) => {
    setSelectedProduct(product);
  }, []);

  const fetchProducts = useCallback(async (query: string) => {
    if (!query.trim()) {
      setError('Please enter a search term');
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      // Fetch from Boeing API
      const boeingProducts = await searchProducts({ query });
      
      // Store raw data in Supabase
      await storeRawBoeingData(boeingProducts);
      
      // Transform to normalized products
      const normalizedProducts: NormalizedProduct[] = boeingProducts.map(p => ({
        ...p,
        status: 'fetched' as ProductStatus,
        title: p.name,
        price: null,
        inventory: null,
        availability: null,
        lastModified: new Date().toISOString(),
      }));
      
      dispatch({ type: 'ADD_PRODUCTS', payload: normalizedProducts });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch products');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const enrichProduct = useCallback(async (productId: string) => {
    const product = products.find(p => p.id === productId);
    if (!product) return;

    setActionState(`enrich-${productId}`, true);
    setError(null);

    try {
      const pricing = await getPricingAndInventory(product.partNumber);
      
      const enrichedProduct: NormalizedProduct = {
        ...product,
        price: pricing.price,
        inventory: pricing.inventory,
        availability: pricing.availability,
        status: 'enriched',
        lastModified: new Date().toISOString(),
      };
      
      dispatch({ type: 'UPDATE_PRODUCT', payload: enrichedProduct });
      
      // Update selected product if it's the one being enriched
      if (selectedProduct?.id === productId) {
        setSelectedProduct(enrichedProduct);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to get pricing');
    } finally {
      setActionState(`enrich-${productId}`, false);
    }
  }, [products, selectedProduct, setActionState]);

  const updateProduct = useCallback(async (product: NormalizedProduct) => {
    setActionState(`save-${product.id}`, true);
    setError(null);

    try {
      const updatedProduct = {
        ...product,
        lastModified: new Date().toISOString(),
      };
      
      // Persist to Supabase
      await saveNormalizedProduct(updatedProduct);
      
      dispatch({ type: 'UPDATE_PRODUCT', payload: updatedProduct });
      setSelectedProduct(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save product');
    } finally {
      setActionState(`save-${product.id}`, false);
    }
  }, [setActionState]);

  const publishProduct = useCallback(async (productId: string): Promise<{ success: boolean; error?: string }> => {
    const product = products.find(p => p.id === productId);
    if (!product) return { success: false, error: 'Product not found' };

    setActionState(`publish-${productId}`, true);
    setError(null);

    try {
      const result = await publishToShopify(product);
      
      if (result.success) {
        dispatch({ type: 'SET_STATUS', payload: { id: productId, status: 'published' } });
        return { success: true };
      } else {
        setError(result.error || 'Failed to publish');
        return { success: false, error: result.error };
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to publish';
      setError(errorMessage);
      return { success: false, error: errorMessage };
    } finally {
      setActionState(`publish-${productId}`, false);
    }
  }, [products, setActionState]);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  return {
    products,
    selectedProduct,
    isLoading,
    error,
    actionLoading,
    selectProduct,
    fetchProducts,
    enrichProduct,
    updateProduct,
    publishProduct,
    clearError,
  };
}
