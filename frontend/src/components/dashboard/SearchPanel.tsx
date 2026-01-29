import { useState, useEffect, useRef, useCallback } from 'react';
import { RealtimeChannel } from '@supabase/supabase-js';
import {
  Loader2,
  Upload,
  CloudDownload,
  X,
  CheckCircle2,
  AlertCircle,
  Clock,
  XCircle,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Download,
  Trash2,
  Package,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { BatchStatusResponse, BatchStatus, NormalizedProduct, ProductStatus } from '@/types/product';
import { ProductTable } from './ProductTable';
import { cn } from '@/lib/utils';
import { subscribeToAllStagingUpdates, unsubscribe } from '@/services/realtimeService';

type StatusFilter = 'all' | 'active' | 'completed' | 'failed' | 'cancelled';

// Helper function to strip variant suffix from part numbers (e.g., "WF338109=K3" -> "WF338109")
const stripVariantSuffix = (partNumber: string): string => {
  if (!partNumber) return '';
  return partNumber.split('=')[0];
};

interface SearchPanelProps {
  activeBatches: BatchStatusResponse[];
  isStarting: boolean;
  error: string | null;
  statusFilter: string | null;
  onStartSearch: (partNumbers: string) => Promise<void>;
  onCancelBatch: (batchId: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  onClearError: () => void;
  onSetStatusFilter: (status: string | null) => void;
  onLoadBatchProducts: (batchId: string) => Promise<NormalizedProduct[]>;
  onClearBatchProducts: () => void;
  onBulkPublishBatch: (batchId: string, products: NormalizedProduct[]) => Promise<string | null>;
  onEditProduct: (product: NormalizedProduct) => void;
  onPublishProduct: (product: NormalizedProduct, batchId?: string) => Promise<{ success: boolean; error?: string }>;
  actionLoading: { [key: string]: boolean };
}

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'active', label: 'Active' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
];

export function SearchPanel({
  activeBatches,
  isStarting,
  error,
  statusFilter,
  onStartSearch,
  onCancelBatch,
  onRefresh,
  onClearError,
  onSetStatusFilter,
  onLoadBatchProducts,
  onClearBatchProducts,
  onBulkPublishBatch,
  onEditProduct,
  onPublishProduct,
  actionLoading,
}: SearchPanelProps) {
  const [partNumbersText, setPartNumbersText] = useState('');
  const [expandedBatches, setExpandedBatches] = useState<Set<string>>(new Set());
  const [batchProducts, setBatchProducts] = useState<Record<string, NormalizedProduct[]>>({});
  const [loadingBatches, setLoadingBatches] = useState<Set<string>>(new Set());
  const [publishingBatches, setPublishingBatches] = useState<Set<string>>(new Set());
  const [selectedProducts, setSelectedProducts] = useState<Record<string, NormalizedProduct | null>>({});

  // Realtime subscription reference for product_staging updates
  const stagingChannelRef = useRef<RealtimeChannel | null>(null);

  // Handle realtime product_staging updates
  const handleStagingProductUpdate = useCallback((updatedProduct: any) => {
    // Update the product in all batch products where it exists
    setBatchProducts(prev => {
      const newBatchProducts = { ...prev };
      let updated = false;

      Object.keys(newBatchProducts).forEach(batchId => {
        const products = newBatchProducts[batchId];
        const productIndex = products.findIndex(
          p => p.sku === updatedProduct.sku || p.id === updatedProduct.id
        );

        if (productIndex >= 0) {
          // Merge ALL updated fields from the realtime event, not just status
          const updatedProducts = [...products];
          updatedProducts[productIndex] = {
            ...updatedProducts[productIndex],
            // Merge all fields from the updated product
            ...(updatedProduct.title && { title: updatedProduct.title }),
            ...(updatedProduct.sku && { sku: updatedProduct.sku }),
            ...(updatedProduct.price !== undefined && { price: updatedProduct.price }),
            ...(updatedProduct.net_price !== undefined && { net_price: updatedProduct.net_price }),
            ...(updatedProduct.cost_per_item !== undefined && { cost_per_item: updatedProduct.cost_per_item }),
            ...(updatedProduct.inventory_quantity !== undefined && { inventory: updatedProduct.inventory_quantity }),
            ...(updatedProduct.weight !== undefined && { weight: updatedProduct.weight }),
            ...(updatedProduct.body_html && { body_html: updatedProduct.body_html }),
            ...(updatedProduct.vendor && { vendor: updatedProduct.vendor }),
            ...(updatedProduct.condition && { condition: updatedProduct.condition }),
            ...(updatedProduct.base_uom && { base_uom: updatedProduct.base_uom }),
            ...(updatedProduct.supplier_name && { supplier_name: updatedProduct.supplier_name }),
            ...(updatedProduct.country_of_origin && { country_of_origin: updatedProduct.country_of_origin }),
            ...(updatedProduct.dim_length !== undefined && { dim_length: updatedProduct.dim_length }),
            ...(updatedProduct.dim_width !== undefined && { dim_width: updatedProduct.dim_width }),
            ...(updatedProduct.dim_height !== undefined && { dim_height: updatedProduct.dim_height }),
            ...(updatedProduct.dim_uom && { dim_uom: updatedProduct.dim_uom }),
            ...(updatedProduct.notes && { notes: updatedProduct.notes }),
            // Always update status if present
            status: (updatedProduct.status as ProductStatus) || updatedProducts[productIndex].status,
          };
          newBatchProducts[batchId] = updatedProducts;
          updated = true;
          console.log('[SearchPanel] Product updated via realtime:', updatedProduct.sku, updatedProduct);
        }
      });

      return updated ? newBatchProducts : prev;
    });
  }, []);

  // Set up Supabase Realtime subscription for product_staging updates
  useEffect(() => {
    const hasLoadedProducts = Object.keys(batchProducts).length > 0;

    if (!hasLoadedProducts) {
      // Clean up subscription if no products loaded
      if (stagingChannelRef.current) {
        unsubscribe(stagingChannelRef.current);
        stagingChannelRef.current = null;
      }
      return;
    }

    // Only subscribe if we have products and not already subscribed
    if (!stagingChannelRef.current) {
      console.log('[SearchPanel] Setting up Realtime subscription for product_staging');
      stagingChannelRef.current = subscribeToAllStagingUpdates(handleStagingProductUpdate);
    }

    return () => {
      if (stagingChannelRef.current) {
        console.log('[SearchPanel] Cleaning up Realtime subscription');
        unsubscribe(stagingChannelRef.current);
        stagingChannelRef.current = null;
      }
    };
  }, [batchProducts, handleStagingProductUpdate]);

  const partNumberCount = partNumbersText
    .split(/[,;\n\r]+/)
    .filter(pn => pn.trim().length > 0).length;

  const handleSearch = async () => {
    if (partNumbersText.trim()) {
      await onStartSearch(partNumbersText);
      setPartNumbersText('');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && partNumbersText.trim()) {
      e.preventDefault();
      handleSearch();
    }
  };

  const handleLoadBatchProducts = async (batchId: string) => {
    setLoadingBatches(prev => new Set(prev).add(batchId));
    try {
      const products = await onLoadBatchProducts(batchId);
      setBatchProducts(prev => ({ ...prev, [batchId]: products }));
    } finally {
      setLoadingBatches(prev => {
        const next = new Set(prev);
        next.delete(batchId);
        return next;
      });
    }
  };

  const handleClearBatchProducts = (batchId: string) => {
    setBatchProducts(prev => {
      const next = { ...prev };
      delete next[batchId];
      return next;
    });
    onClearBatchProducts();
  };

  const handleBulkPublishBatch = async (batchId: string) => {
    const products = batchProducts[batchId];
    if (!products || products.length === 0) return;

    setPublishingBatches(prev => new Set(prev).add(batchId));
    try {
      await onBulkPublishBatch(batchId, products);

      // After publishing starts, wait a moment then refresh products to show updated status
      setTimeout(async () => {
        try {
          const updatedProducts = await onLoadBatchProducts(batchId);
          setBatchProducts(prev => ({ ...prev, [batchId]: updatedProducts }));
        } catch (err) {
          console.error('Failed to refresh products after publish:', err);
        }
      }, 3000); // Wait 3 seconds for publish to process
    } finally {
      setPublishingBatches(prev => {
        const next = new Set(prev);
        next.delete(batchId);
        return next;
      });
    }
  };

  const handleSelectProduct = (batchId: string, product: NormalizedProduct | null) => {
    setSelectedProducts(prev => ({ ...prev, [batchId]: product }));
  };

  const toggleBatchExpand = (batchId: string) => {
    setExpandedBatches(prev => {
      const next = new Set(prev);
      if (next.has(batchId)) {
        next.delete(batchId);
      } else {
        next.add(batchId);
      }
      return next;
    });
  };

  const getStatusIcon = (status: BatchStatus) => {
    switch (status) {
      case 'pending':
        return <Clock className="h-4 w-4 text-muted-foreground" />;
      case 'processing':
        return <Loader2 className="h-4 w-4 animate-spin text-primary" />;
      case 'completed':
        return <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
      case 'failed':
        return <AlertCircle className="h-4 w-4 text-destructive" />;
      case 'cancelled':
        return <XCircle className="h-4 w-4 text-muted-foreground" />;
      default:
        return null;
    }
  };

  const getStatusBadgeClass = (status: BatchStatus) => {
    switch (status) {
      case 'pending':
        return 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400';
      case 'processing':
        return 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400';
      case 'completed':
        return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400';
      case 'failed':
        return 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400';
      case 'cancelled':
        return 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-500';
      default:
        return '';
    }
  };

  const formatTime = (dateString: string) => {
    return new Date(dateString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  };

  // Format batch type for display - shows pipeline stage with status awareness
  const formatBatchType = (batchType: string, status: BatchStatus) => {
    // When completed, show past-tense labels
    if (status === 'completed') {
      switch (batchType) {
        case 'search':
          return 'Fetched';
        case 'normalized':
          return 'Normalized -> Ready to Publish';
        case 'publishing':
          return 'Published';
        case 'publish':
          return 'Published';
        default:
          return batchType;
      }
    }

    // For active/in-progress states
    switch (batchType) {
      case 'search':
        return 'Fetching';
      case 'normalized':
        return 'Normalizing';
      case 'publishing':
        return 'Publishing';
      case 'publish':
        return 'Publishing';
      default:
        return batchType;
    }
  };

  const activeBatchesCount = activeBatches.filter(
    b => b.status === 'pending' || b.status === 'processing'
  ).length;

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Search Input Section */}
      <div className="bg-card border-b border-border px-6 py-5">
        <div className="max-w-4xl">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
              <CloudDownload className="h-4 w-4 text-primary" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-foreground">Fetch Parts</h2>
              <p className="text-xs text-muted-foreground">
                Enter part numbers to fetch product data from Boeing
              </p>
            </div>
          </div>

          {/* Error Alert */}
          {error && (
            <div className="mb-4 bg-destructive/10 border border-destructive/20 rounded-lg p-3 flex items-start justify-between">
              <div className="flex items-start gap-2">
                <AlertCircle className="h-4 w-4 text-destructive mt-0.5 flex-shrink-0" />
                <span className="text-sm text-destructive">{error}</span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={onClearError}
                className="h-6 w-6 p-0 hover:bg-destructive/10"
              >
                <X className="h-3 w-3 text-destructive" />
              </Button>
            </div>
          )}

          {/* Fetch Input */}
          <div className="flex gap-3">
            <div className="flex-1 relative">
              <CloudDownload className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Enter part numbers (comma, semicolon, or space separated)"
                value={partNumbersText}
                onChange={e => setPartNumbersText(e.target.value)}
                onKeyDown={handleKeyDown}
                className="pl-9 pr-20 font-mono text-sm h-10"
              />
              {partNumberCount > 0 && (
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full font-medium">
                  {partNumberCount} part{partNumberCount !== 1 ? 's' : ''}
                </span>
              )}
            </div>
            <Button
              onClick={handleSearch}
              disabled={isStarting || partNumberCount === 0}
              className="h-10 px-5"
            >
              {isStarting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <>
                  <CloudDownload className="h-4 w-4 mr-2" />
                  Fetch
                </>
              )}
            </Button>
          </div>

          {/* Helper text */}
          <p className="mt-2 text-xs text-muted-foreground">
            Tip: You can enter multiple part numbers separated by commas, semicolons, or spaces
          </p>
        </div>
      </div>

      {/* Batches Section */}
      <div className="flex-1 overflow-auto bg-muted/30">
        <div className="px-6 py-4">
          {/* Section Header */}
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-medium text-foreground">Recent Requests</h3>
              {activeBatchesCount > 0 && (
                <span className="bg-primary text-primary-foreground text-xs px-2 py-0.5 rounded-full font-medium">
                  {activeBatchesCount} active
                </span>
              )}
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onRefresh()}
              className="h-8 text-xs"
            >
              <RefreshCw className="h-3 w-3 mr-1" />
              Refresh
            </Button>
          </div>

          {/* Status Filter Tabs and Pipeline Legend - Same Row */}
          <div className="flex items-center justify-between mb-4">
            {/* Status Filter Tabs */}
            <div className="flex items-center gap-1 p-1 bg-muted rounded-lg w-fit">
              {STATUS_FILTERS.map((filter) => (
                <button
                  key={filter.value}
                  onClick={() => onSetStatusFilter(filter.value === 'all' ? null : filter.value)}
                  className={cn(
                    "px-3 py-1.5 text-xs font-medium rounded-md transition-colors",
                    (statusFilter === filter.value || (filter.value === 'all' && !statusFilter))
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  {filter.label}
                </button>
              ))}
            </div>

            {/* Pipeline Legend */}
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
              <span className="font-medium">Pipeline stages:</span>
              <div className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-slate-400" />
                <span>Extracted</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-amber-400" />
                <span>Normalized</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-emerald-500" />
                <span>Published</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="h-2 w-2 rounded-full bg-red-500" />
                <span>No Stock</span>
              </div>
            </div>
          </div>

          {/* Empty State */}
          {activeBatches.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <div className="rounded-full bg-muted p-4 mb-4">
                <Package className="h-8 w-8 text-muted-foreground" />
              </div>
              <h3 className="text-base font-medium text-foreground mb-1">No requests yet</h3>
              <p className="text-sm text-muted-foreground max-w-sm">
                Enter part numbers above to fetch product data from Boeing.
              </p>
            </div>
          )}

          {/* Batch Cards */}
          <div className="space-y-3">
            {activeBatches
              // Show all pipeline batches (search, normalized, publishing)
              // Filter out old-style standalone "publish" batches (legacy)
              .filter(batch => ['search', 'normalized', 'publishing'].includes(batch.batch_type))
              .slice(0, 10)
              .map(batch => {
              const hasProducts = batchProducts[batch.id] && batchProducts[batch.id].length > 0;
              // Count only products that can be published:
              // - Not already published
              // - Has inventory > 0
              // - Has a price > 0 (price, net_price, or cost_per_item)
              const unpublishedCount = hasProducts
                ? batchProducts[batch.id].filter(p => {
                    if (p.status === 'published') return false;
                    const hasInventory = p.inventory !== null && p.inventory !== undefined && p.inventory > 0;
                    const hasPrice = (p.price !== null && p.price !== undefined && p.price > 0) ||
                                     (p.net_price !== null && p.net_price !== undefined && p.net_price > 0) ||
                                     (p.cost_per_item !== null && p.cost_per_item !== undefined && p.cost_per_item > 0);
                    return hasInventory && hasPrice;
                  }).length
                : 0;

              return (
                <div
                  key={batch.id}
                  className={cn(
                    "border rounded-lg bg-card shadow-sm transition-shadow",
                    hasProducts && "shadow-md"
                  )}
                >
                  {/* Batch Header */}
                  <div className="px-4 py-3">
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        {getStatusIcon(batch.status)}
                        <span className="text-sm font-medium">
                          {formatBatchType(batch.batch_type, batch.status)}
                        </span>
                        <span className={cn(
                          "text-xs px-2 py-0.5 rounded-full font-medium",
                          getStatusBadgeClass(batch.status)
                        )}>
                          {batch.status}
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">
                          {formatTime(batch.created_at)}
                        </span>
                        {['pending', 'processing'].includes(batch.status) && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onCancelBatch(batch.id)}
                            className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive"
                          >
                            Cancel
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => toggleBatchExpand(batch.id)}
                          className="h-7 w-7 p-0"
                        >
                          {expandedBatches.has(batch.id) ? (
                            <ChevronUp className="h-4 w-4" />
                          ) : (
                            <ChevronDown className="h-4 w-4" />
                          )}
                        </Button>
                      </div>
                    </div>

                    {/* Progress Bar */}
                    <div className="mb-2">
                      <Progress
                        value={batch.progress_percent}
                        className={cn(
                          "h-1.5",
                          batch.status === 'completed' && "[&>div]:bg-emerald-500"
                        )}
                      />
                    </div>

                    {/* Progress Stats */}
                    <div className="flex items-center justify-between text-xs text-muted-foreground">
                      <div className="flex items-center gap-3">
                        <span>Total: <span className="font-medium text-foreground">{batch.total_items}</span></span>
                        {batch.batch_type === 'search' ? (
                          <>
                            <span>Extracted: <span className="font-medium text-foreground">{batch.extracted_count}</span></span>
                            <span>Normalized: <span className="font-medium text-foreground">{batch.normalized_count}</span></span>
                          </>
                        ) : (
                          <span>Published: <span className="font-medium text-foreground">{batch.published_count}</span></span>
                        )}
                      </div>
                      <span className="font-medium text-foreground">
                        {batch.progress_percent.toFixed(0)}%
                      </span>
                    </div>

                  </div>

                  {/* Action Buttons - visible throughout pipeline (search, normalized, publishing) */}
                  {(batch.normalized_count > 0 || batch.batch_type === 'publishing') && (
                    <div className="px-4 py-2 border-t border-border bg-muted/30 flex items-center gap-2">
                      {/* Toggle button: Load Products / Hide */}
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          if (hasProducts) {
                            handleClearBatchProducts(batch.id);
                          } else {
                            handleLoadBatchProducts(batch.id);
                          }
                        }}
                        disabled={loadingBatches.has(batch.id)}
                        className="h-8 text-xs"
                      >
                        {loadingBatches.has(batch.id) ? (
                          <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                        ) : hasProducts ? (
                          <ChevronUp className="h-3 w-3 mr-1.5" />
                        ) : (
                          <Download className="h-3 w-3 mr-1.5" />
                        )}
                        {hasProducts ? 'Hide' : `Load Products (${batch.normalized_count})`}
                      </Button>
                      {/* Only show Publish All button for completed search/normalized batches, not during publishing */}
                      {hasProducts && batch.status === 'completed' && ['search', 'normalized'].includes(batch.batch_type) && (
                        <Button
                          variant="default"
                          size="sm"
                          onClick={() => handleBulkPublishBatch(batch.id)}
                          disabled={publishingBatches.has(batch.id) || unpublishedCount === 0}
                          className="h-8 text-xs"
                        >
                          {publishingBatches.has(batch.id) ? (
                            <Loader2 className="h-3 w-3 mr-1.5 animate-spin" />
                          ) : (
                            <Upload className="h-3 w-3 mr-1.5" />
                          )}
                          Publish All ({unpublishedCount})
                        </Button>
                      )}
                    </div>
                  )}

                  {/* Products Table */}
                  {hasProducts && (
                    <div className="border-t border-border">
                      <ProductTable
                        products={batchProducts[batch.id]}
                        selectedProduct={selectedProducts[batch.id] || null}
                        actionLoading={actionLoading}
                        batchId={batch.id}
                        onSelectProduct={(product) => handleSelectProduct(batch.id, product)}
                        onEditProduct={onEditProduct}
                        onPublishProduct={onPublishProduct}
                      />
                    </div>
                  )}

                  {/* Expanded Details - Pipeline Tracking */}
                  {expandedBatches.has(batch.id) && (
                    <div className="px-4 py-3 border-t border-border bg-muted/20 space-y-4">
                      {/* Batch ID */}
                      <div className="text-xs">
                        <span className="text-muted-foreground">Batch ID: </span>
                        <span className="font-mono text-foreground">{batch.id}</span>
                      </div>

                      {/* Pipeline Summary Cards */}
                      {(() => {
                        // Calculate not queued count (extracted but not in publish queue)
                        // Strip variant suffix before comparing (e.g., "WF338109=K3" -> "WF338109")
                        const publishedStripped = batch.publish_part_numbers?.map(stripVariantSuffix) || [];
                        // Only show skipped count for publishing batches that have publish_part_numbers populated
                        const notQueuedCount = batch.batch_type === 'publishing' &&
                          batch.part_numbers &&
                          batch.publish_part_numbers &&
                          batch.publish_part_numbers.length > 0
                          ? batch.part_numbers.filter(pn => !publishedStripped.includes(stripVariantSuffix(pn))).length
                          : 0;
                        return (
                          <div className="grid grid-cols-4 gap-3">
                            {/* Extracted/Searched */}
                            <div className="bg-background rounded-lg border p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <span className="h-2.5 w-2.5 rounded-full bg-slate-400" />
                                <span className="text-xs font-medium text-foreground">Extracted</span>
                              </div>
                              <div className="text-2xl font-bold text-foreground">
                                {batch.part_numbers?.length || 0}
                              </div>
                              <div className="text-xs text-muted-foreground">part numbers fetched</div>
                            </div>

                            {/* Published */}
                            <div className="bg-background rounded-lg border p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
                                <span className="text-xs font-medium text-foreground">Published</span>
                              </div>
                              <div className="text-2xl font-bold text-emerald-600 dark:text-emerald-400">
                                {batch.published_count}
                              </div>
                              <div className="text-xs text-muted-foreground">
                                {batch.publish_part_numbers?.length
                                  ? `of ${batch.publish_part_numbers.length} queued`
                                  : 'to Shopify'}
                              </div>
                            </div>

                            {/* Failed (Shopify errors) */}
                            <div className="bg-background rounded-lg border p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <span className="h-2.5 w-2.5 rounded-full bg-red-500" />
                                <span className="text-xs font-medium text-foreground">Failed</span>
                              </div>
                              <div className="text-2xl font-bold text-red-600 dark:text-red-400">
                                {batch.failed_count}
                              </div>
                              <div className="text-xs text-muted-foreground">Shopify errors</div>
                            </div>

                            {/* Not Queued (no inventory/price) */}
                            <div className="bg-background rounded-lg border p-3">
                              <div className="flex items-center gap-2 mb-2">
                                <span className="h-2.5 w-2.5 rounded-full bg-amber-500" />
                                <span className="text-xs font-medium text-foreground">Skipped</span>
                              </div>
                              <div className="text-2xl font-bold text-amber-600 dark:text-amber-400">
                                {notQueuedCount}
                              </div>
                              <div className="text-xs text-muted-foreground">no inventory/price</div>
                            </div>
                          </div>
                        );
                      })()}

                      {/* Extracted Part Numbers Section */}
                      {batch.part_numbers && batch.part_numbers.length > 0 && (
                        <div className="text-xs">
                          <div className="flex items-center gap-2 mb-2">
                            <Package className="h-3.5 w-3.5 text-slate-500" />
                            <span className="font-medium text-foreground">
                              Extracted Part Numbers ({batch.part_numbers.length})
                            </span>
                          </div>
                          <div className="flex flex-wrap gap-1.5 max-h-[100px] overflow-y-auto p-2 bg-background rounded border">
                            {batch.part_numbers.map((pn, idx) => {
                              // Strip variant suffix for comparison (e.g., "WF338109=K3" -> "WF338109")
                              const pnStripped = stripVariantSuffix(pn);
                              const publishStripped = batch.publish_part_numbers?.map(stripVariantSuffix) || [];
                              const failedStripped = batch.failed_items?.map(f => stripVariantSuffix(f.part_number)) || [];

                              // For publishing batches: ALL extracted parts should show green
                              // because extraction and normalization are complete at that stage
                              // Real-time status updates only affect the "Publishing to Shopify" section
                              const isPublishingStage = batch.batch_type === 'publishing';

                              const isFailed = failedStripped.includes(pnStripped);
                              const isQueued = publishStripped.includes(pnStripped);

                              // In publishing stage, all parts are "extracted/normalized" (green)
                              // In search/normalized stage, show queued parts as blue
                              const isExtracted = isPublishingStage || batch.batch_type === 'normalized';

                              return (
                                <span
                                  key={idx}
                                  className={cn(
                                    "font-mono px-2 py-0.5 rounded text-xs",
                                    isFailed
                                      ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                                      : isExtracted
                                        ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                                        : isQueued
                                          ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
                                          : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400"
                                  )}
                                  title={isFailed ? 'Failed' : isExtracted ? 'Extracted & Normalized' : isQueued ? 'Queued for publishing' : 'Fetched'}
                                >
                                  {pn}
                                </span>
                              );
                            })}
                          </div>
                        </div>
                      )}

                      {/* Published/Publishing Part Numbers Section */}
                      {batch.publish_part_numbers && batch.publish_part_numbers.length > 0 && (() => {
                        // Get real-time product statuses from batchProducts
                        const loadedProducts = batchProducts[batch.id] || [];
                        const getProductStatus = (partNumber: string) => {
                          const pnStripped = stripVariantSuffix(partNumber);
                          const product = loadedProducts.find(p =>
                            stripVariantSuffix(p.sku) === pnStripped || p.sku === partNumber
                          );
                          return product?.status;
                        };

                        // Count actually published items using real-time status
                        const publishedCount = loadedProducts.length > 0
                          ? batch.publish_part_numbers.filter(pn => getProductStatus(pn) === 'published').length
                          : batch.publish_part_numbers.filter(pn =>
                              !batch.failed_items?.some(f => f.part_number === pn)
                            ).length;

                        return (
                        <div className="text-xs">
                          <div className="flex items-center gap-2 mb-2">
                            <Upload className={cn(
                              "h-3.5 w-3.5",
                              batch.batch_type === 'publishing' && batch.status === 'processing'
                                ? "text-blue-500"
                                : "text-emerald-500"
                            )} />
                            <span className="font-medium text-foreground">
                              {batch.batch_type === 'publishing' && batch.status === 'processing'
                                ? 'Publishing to Shopify'
                                : 'Published to Shopify'
                              } ({publishedCount} of {batch.publish_part_numbers.length})
                              {batch.batch_type === 'publishing' && batch.status === 'processing' && (
                                <Loader2 className="h-3 w-3 ml-1.5 inline animate-spin" />
                              )}
                            </span>
                          </div>
                          <div className={cn(
                            "flex flex-wrap gap-1.5 max-h-[100px] overflow-y-auto p-2 bg-background rounded border",
                            batch.batch_type === 'publishing' && batch.status === 'processing'
                              ? "border-blue-200 dark:border-blue-800"
                              : "border-emerald-200 dark:border-emerald-800"
                          )}>
                            {batch.publish_part_numbers.map((pn, idx) => {
                              const isFailed = batch.failed_items?.some(f => f.part_number === pn);
                              // Use real-time product status if available
                              const productStatus = getProductStatus(pn);
                              const isPublishedRealtime = productStatus === 'published';
                              const isPublishing = !isPublishedRealtime && !isFailed &&
                                batch.batch_type === 'publishing' && batch.status === 'processing';
                              return (
                                <span
                                  key={idx}
                                  className={cn(
                                    "font-mono px-2 py-0.5 rounded text-xs",
                                    isFailed
                                      ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 line-through"
                                      : isPublishedRealtime
                                        ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                                        : isPublishing
                                          ? "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400"
                                          : "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                                  )}
                                  title={isFailed ? 'Failed to publish' : isPublishing ? 'Publishing...' : 'Successfully published'}
                                >
                                  {pn}
                                </span>
                              );
                            })}
                          </div>
                        </div>
                        );
                      })()}

                      {/* Not Published Section - Part numbers that were extracted but not queued for publishing */}
                      {batch.part_numbers && batch.publish_part_numbers && batch.batch_type === 'publishing' && (() => {
                        // Strip variant suffix for comparison (e.g., "WF338109=K3" -> "WF338109")
                        const publishStripped = batch.publish_part_numbers?.map(stripVariantSuffix) || [];
                        const notPublished = batch.part_numbers.filter(pn =>
                          !publishStripped.includes(stripVariantSuffix(pn))
                        );
                        if (notPublished.length === 0) return null;
                        return (
                          <div className="text-xs">
                            <div className="flex items-center gap-2 mb-2">
                              <XCircle className="h-3.5 w-3.5 text-amber-500" />
                              <span className="font-medium text-foreground">
                                Not Queued for Publishing ({notPublished.length})
                              </span>
                              <span className="text-muted-foreground">(no inventory or price)</span>
                            </div>
                            <div className="flex flex-wrap gap-1.5 max-h-[100px] overflow-y-auto p-2 bg-background rounded border border-amber-200 dark:border-amber-800">
                              {notPublished.map((pn, idx) => (
                                <span
                                  key={idx}
                                  className="font-mono px-2 py-0.5 rounded text-xs bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                                  title="Not queued - missing inventory or price"
                                >
                                  {pn}
                                </span>
                              ))}
                            </div>
                          </div>
                        );
                      })()}

                      {/* Error Message */}
                      {batch.error_message && (
                        <div className="text-xs">
                          <div className="flex items-center gap-2 mb-1">
                            <AlertCircle className="h-3.5 w-3.5 text-destructive" />
                            <span className="font-medium text-destructive">Error</span>
                          </div>
                          <div className="text-destructive bg-destructive/10 rounded p-2 border border-destructive/20">
                            {batch.error_message}
                          </div>
                        </div>
                      )}

                      {/* Failed Items Table - with detailed error messages */}
                      {batch.failed_items && batch.failed_items.length > 0 && (
                        <div className="text-xs">
                          <div className="flex items-center gap-2 mb-2">
                            <AlertCircle className="h-3.5 w-3.5 text-destructive" />
                            <span className="font-medium text-destructive">
                              Failed Items ({batch.failed_items.length})
                            </span>
                          </div>
                          <div className="bg-destructive/5 rounded border border-destructive/20 overflow-hidden">
                            <div className="max-h-[150px] overflow-y-auto">
                              <table className="w-full text-xs">
                                <thead className="bg-destructive/10 sticky top-0">
                                  <tr>
                                    <th className="text-left px-3 py-1.5 font-medium text-destructive">Part Number</th>
                                    <th className="text-left px-3 py-1.5 font-medium text-destructive">Reason</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-destructive/10">
                                  {batch.failed_items.map((item, idx) => (
                                    <tr key={idx} className="hover:bg-destructive/5">
                                      <td className="px-3 py-1.5 font-mono text-foreground whitespace-nowrap">
                                        {item.part_number}
                                      </td>
                                      <td className="px-3 py-1.5 text-muted-foreground">
                                        {item.error}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
