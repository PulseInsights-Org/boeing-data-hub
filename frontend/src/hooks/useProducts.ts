import { useState, useCallback, useReducer } from 'react';
import { NormalizedProduct, ProductStatus } from '@/types/product';
import { searchProducts } from '@/services/boeingService';
import { saveNormalizedProduct } from '@/services/supabaseService';
import { publishToShopify } from '@/services/shopifyService';
import { getStagingProducts, StagingProduct } from '@/services/bulkService';

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
  loadStagingProducts: (limit?: number, offset?: number) => Promise<void>;
  updateProduct: (product: NormalizedProduct) => Promise<void>;
  publishProduct: (productId: string) => Promise<{ success: boolean; error?: string }>;
  clearError: () => void;
  clearProducts: () => void;
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
      // Fetch from backend Boeing API (already normalized, with price/inventory
      // and persisted to Supabase on the server side).
      const boeingProducts = await searchProducts({ query });

      const now = new Date().toISOString();
      const normalizedProducts: NormalizedProduct[] = boeingProducts.map((p: any) => ({
        // Spread all backend fields
        ...p,
        // Map backend field names to frontend field names
        id: p.aviall_part_number || p.id || p.sku,
        partNumber: p.aviall_part_number || p.sku || '',
        name: p.name || p.title || '',
        description: p.description || '',
        manufacturer: p.manufacturer || p.supplier_name || '',
        length: p.dim_length ?? null,
        width: p.dim_width ?? null,
        height: p.dim_height ?? null,
        dimensionUom: p.dim_uom || '',
        weight: p.weight ?? null,
        weightUnit: p.weight_uom || '',
        // Price mapping - backend uses cost_per_item and net_price
        price: p.cost_per_item ?? p.net_price ?? null,
        inventory: p.inventory_quantity ?? null,
        availability: p.inventory_status ?? null,
        currency: p.currency ?? 'USD',
        // Status fields
        status: 'fetched' as ProductStatus,
        title: p.title || p.name || p.aviall_part_number || '',
        lastModified: now,
        // Keep raw data
        rawBoeingData: {},
      }));

      dispatch({ type: 'ADD_PRODUCTS', payload: normalizedProducts });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch products');
    } finally {
      setIsLoading(false);
    }
  }, []);

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

  const clearProducts = useCallback(() => {
    dispatch({ type: 'SET_PRODUCTS', payload: [] });
  }, []);

  /**
   * Load normalized products from product_staging table.
   * These are products that have been processed through bulk search operations.
   */
  const loadStagingProducts = useCallback(async (limit: number = 100, offset: number = 0) => {
    setIsLoading(true);
    setError(null);

    try {
      const response = await getStagingProducts(limit, offset);
      const now = new Date().toISOString();

      // Convert staging products to NormalizedProduct format
      const normalizedProducts: NormalizedProduct[] = response.products.map((p: StagingProduct) => ({
        // Core identifiers
        id: p.id || p.sku,
        partNumber: p.sku || p.id || '',
        sku: p.sku,

        // Names and descriptions
        name: p.boeing_name || p.title || p.sku || '',
        title: p.title || p.boeing_name || p.sku || '',
        description: p.boeing_description || p.body_html || '',

        // Manufacturer/vendor
        manufacturer: p.supplier_name || p.vendor || '',
        vendor: p.vendor,
        supplier_name: p.supplier_name,

        // Dimensions
        length: p.dim_length,
        width: p.dim_width,
        height: p.dim_height,
        dim_length: p.dim_length,
        dim_width: p.dim_width,
        dim_height: p.dim_height,
        dimensionUom: p.dim_uom || '',
        dim_uom: p.dim_uom,

        // Weight
        weight: p.weight,
        weightUnit: p.weight_unit || '',
        weight_uom: p.weight_unit,

        // Pricing
        price: p.cost_per_item ?? p.net_price ?? p.price ?? null,
        cost_per_item: p.cost_per_item,
        net_price: p.net_price,
        list_price: p.list_price,
        currency: p.currency ?? 'USD',

        // Inventory
        inventory: p.inventory_quantity,
        inventory_quantity: p.inventory_quantity,
        availability: p.inventory_status,
        inventory_status: p.inventory_status,

        // Status
        status: (p.status as ProductStatus) || 'fetched',
        lastModified: p.updated_at || now,

        // Additional fields
        country_of_origin: p.country_of_origin,
        base_uom: p.base_uom,
        hazmat_code: p.hazmat_code,
        faa_approval_code: p.faa_approval_code,
        eccn: p.eccn,
        schedule_b_code: p.schedule_b_code,
        condition: p.condition,
        pma: p.pma,

        // Images
        product_image: p.boeing_image_url || p.image_url,
        thumbnail_image: p.boeing_thumbnail_url,

        // Distribution source
        distrSrc: 'BDI',
        pnAUrl: '',

        // Raw Boeing data now fetched separately via /api/products/raw-data endpoint
        rawBoeingData: {},
      }));

      dispatch({ type: 'SET_PRODUCTS', payload: normalizedProducts });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load staging products');
    } finally {
      setIsLoading(false);
    }
  }, []);

  return {
    products,
    selectedProduct,
    isLoading,
    error,
    actionLoading,
    selectProduct,
    fetchProducts,
    loadStagingProducts,
    updateProduct,
    publishProduct,
    clearError,
    clearProducts,
  };
}
