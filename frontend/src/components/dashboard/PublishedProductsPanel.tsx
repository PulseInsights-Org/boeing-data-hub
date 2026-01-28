import { useState } from 'react';
import {
  Search,
  RefreshCw,
  Package,
  ExternalLink,
  Loader2,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  ShoppingBag,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import { PublishedProduct } from '@/hooks/usePublishedProducts';

interface PublishedProductsPanelProps {
  products: PublishedProduct[];
  total: number;
  isLoading: boolean;
  error: string | null;
  searchQuery: string;
  hasMore: boolean;
  shopifyStoreDomain: string | null;
  onSearchChange: (query: string) => void;
  onRefresh: () => Promise<void>;
  onLoadMore: () => Promise<void>;
}

export function PublishedProductsPanel({
  products,
  total,
  isLoading,
  error,
  searchQuery,
  hasMore,
  shopifyStoreDomain,
  onSearchChange,
  onRefresh,
  onLoadMore,
}: PublishedProductsPanelProps) {
  const [expandedProducts, setExpandedProducts] = useState<Set<string>>(new Set());

  const toggleProductExpand = (productId: string) => {
    setExpandedProducts(prev => {
      const next = new Set(prev);
      if (next.has(productId)) {
        next.delete(productId);
      } else {
        next.add(productId);
      }
      return next;
    });
  };

  const formatPrice = (price: number | null, currency: string) => {
    if (price === null) return '-';
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency || 'USD',
    }).format(price);
  };

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header Section */}
      <div className="bg-card border-b border-border px-6 py-5">
        <div className="max-w-4xl">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500/10">
              <ShoppingBag className="h-4 w-4 text-emerald-500" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-foreground">Published Products</h2>
              <p className="text-xs text-muted-foreground">
                View all products published to Shopify ({total} total)
              </p>
            </div>
          </div>

          {/* Search Input */}
          <div className="flex gap-3">
            <div className="flex-1 relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search by part number..."
                value={searchQuery}
                onChange={e => onSearchChange(e.target.value)}
                className="pl-9"
              />
            </div>
            <Button
              variant="outline"
              onClick={onRefresh}
              disabled={isLoading}
            >
              {isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>
      </div>

      {/* Products List */}
      <div className="flex-1 overflow-auto bg-muted/30">
        <div className="px-6 py-4">
          {/* Error Alert */}
          {error && (
            <div className="mb-4 bg-destructive/10 border border-destructive/20 rounded-lg p-3 flex items-start gap-2">
              <AlertCircle className="h-4 w-4 text-destructive mt-0.5 flex-shrink-0" />
              <span className="text-sm text-destructive">{error}</span>
            </div>
          )}

          {/* Empty State */}
          {!isLoading && products.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <div className="rounded-full bg-muted p-4 mb-4">
                <Package className="h-8 w-8 text-muted-foreground" />
              </div>
              <h3 className="text-base font-medium text-foreground mb-1">
                {searchQuery ? 'No products found' : 'No published products yet'}
              </h3>
              <p className="text-sm text-muted-foreground max-w-sm">
                {searchQuery
                  ? `No products match "${searchQuery}". Try a different search term.`
                  : 'Products will appear here once they are published to Shopify.'}
              </p>
            </div>
          )}

          {/* Loading State */}
          {isLoading && products.length === 0 && (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          )}

          {/* Products Table */}
          {products.length > 0 && (
            <div className="bg-card border rounded-lg overflow-hidden">
              <table className="w-full">
                <thead className="bg-muted/50 border-b">
                  <tr>
                    <th className="text-left px-4 py-3 text-xs font-medium text-muted-foreground">Part Number</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-muted-foreground">Title</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-muted-foreground">Vendor</th>
                    <th className="text-right px-4 py-3 text-xs font-medium text-muted-foreground">Price</th>
                    <th className="text-right px-4 py-3 text-xs font-medium text-muted-foreground">Inventory</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-muted-foreground">Shopify</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-muted-foreground">Updated</th>
                    <th className="w-10"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {products.map(product => (
                    <>
                      <tr
                        key={product.id}
                        className={cn(
                          "hover:bg-muted/30 transition-colors",
                          expandedProducts.has(product.id) && "bg-muted/20"
                        )}
                      >
                        <td className="px-4 py-3">
                          <span className="font-mono text-sm font-medium">{product.sku}</span>
                        </td>
                        <td className="px-4 py-3">
                          <span className="text-sm line-clamp-1 max-w-[200px]" title={product.title}>
                            {product.title || '-'}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <span className="text-sm text-muted-foreground">{product.vendor || '-'}</span>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <span className="text-sm font-medium">
                            {formatPrice(product.price, product.currency)}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <span className={cn(
                            "text-sm",
                            product.inventory_quantity && product.inventory_quantity > 0
                              ? "text-emerald-600"
                              : "text-muted-foreground"
                          )}>
                            {product.inventory_quantity ?? '-'}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          {product.shopify_product_id && shopifyStoreDomain ? (
                            <a
                              href={`https://admin.shopify.com/store/${shopifyStoreDomain}/products/${product.shopify_product_id}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                            >
                              <ExternalLink className="h-3 w-3" />
                              View
                            </a>
                          ) : (
                            <span className="text-xs text-muted-foreground">-</span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          <span className="text-xs text-muted-foreground">
                            {formatDate(product.updated_at)}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => toggleProductExpand(product.id)}
                            className="h-7 w-7 p-0"
                          >
                            {expandedProducts.has(product.id) ? (
                              <ChevronUp className="h-4 w-4" />
                            ) : (
                              <ChevronDown className="h-4 w-4" />
                            )}
                          </Button>
                        </td>
                      </tr>
                      {/* Expanded Details */}
                      {expandedProducts.has(product.id) && (
                        <tr key={`${product.id}-details`}>
                          <td colSpan={8} className="bg-muted/20 px-4 py-4">
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs">
                              <div>
                                <span className="text-muted-foreground">Cost per Item</span>
                                <p className="font-medium mt-0.5">
                                  {formatPrice(product.cost_per_item, product.currency)}
                                </p>
                              </div>
                              <div>
                                <span className="text-muted-foreground">Dimensions</span>
                                <p className="font-medium mt-0.5">
                                  {product.dim_length && product.dim_width && product.dim_height
                                    ? `${product.dim_length} × ${product.dim_width} × ${product.dim_height} ${product.dim_uom || ''}`
                                    : '-'}
                                </p>
                              </div>
                              <div>
                                <span className="text-muted-foreground">Weight</span>
                                <p className="font-medium mt-0.5">
                                  {product.weight
                                    ? `${product.weight} ${product.weight_unit || ''}`
                                    : '-'}
                                </p>
                              </div>
                              <div>
                                <span className="text-muted-foreground">Country of Origin</span>
                                <p className="font-medium mt-0.5">{product.country_of_origin || '-'}</p>
                              </div>
                              <div className="col-span-2 md:col-span-4">
                                <span className="text-muted-foreground">Description</span>
                                <p className="font-medium mt-0.5 line-clamp-3">
                                  {product.body_html || '-'}
                                </p>
                              </div>
                              {product.image_url && (
                                <div className="col-span-2 md:col-span-4">
                                  <span className="text-muted-foreground">Image</span>
                                  <div className="mt-1">
                                    <img
                                      src={product.image_url}
                                      alt={product.title}
                                      className="h-20 w-20 object-cover rounded border"
                                    />
                                  </div>
                                </div>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  ))}
                </tbody>
              </table>

              {/* Load More */}
              {hasMore && (
                <div className="px-4 py-3 border-t bg-muted/30 flex justify-center">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={onLoadMore}
                    disabled={isLoading}
                  >
                    {isLoading ? (
                      <Loader2 className="h-4 w-4 animate-spin mr-2" />
                    ) : null}
                    Load More ({products.length} of {total})
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
