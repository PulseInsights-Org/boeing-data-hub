export type ProductStatus = 'fetched' | 'enriched' | 'published';

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
}
