import { useState } from 'react';
import {
  Loader2,
  Upload,
  Search,
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
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Progress } from '@/components/ui/progress';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { BatchStatusResponse, BatchStatus, NormalizedProduct } from '@/types/product';
import { ProductTable } from './ProductTable';

interface BulkOperationsPanelProps {
  activeBatches: BatchStatusResponse[];
  isStarting: boolean;
  error: string | null;
  onStartBulkSearch: (partNumbers: string) => Promise<void>;
  onCancelBatch: (batchId: string) => Promise<void>;
  onRefresh: () => Promise<void>;
  onClearError: () => void;
  onLoadBatchProducts: () => Promise<NormalizedProduct[]>;
  onClearBatchProducts: () => void;
  onBulkPublishBatch: (batchId: string, products: NormalizedProduct[]) => Promise<void>;
  onEditProduct: (product: NormalizedProduct) => void;
  onPublishProduct: (productId: string) => Promise<{ success: boolean; error?: string }>;
  actionLoading: { [key: string]: boolean };
}

export function BulkOperationsPanel({
  activeBatches,
  isStarting,
  error,
  onStartBulkSearch,
  onCancelBatch,
  onRefresh,
  onClearError,
  onLoadBatchProducts,
  onClearBatchProducts,
  onBulkPublishBatch,
  onEditProduct,
  onPublishProduct,
  actionLoading,
}: BulkOperationsPanelProps) {
  const [partNumbersText, setPartNumbersText] = useState('');
  const [isExpanded, setIsExpanded] = useState(true);
  const [expandedBatches, setExpandedBatches] = useState<Set<string>>(new Set());
  const [batchProducts, setBatchProducts] = useState<Record<string, NormalizedProduct[]>>({});
  const [loadingBatches, setLoadingBatches] = useState<Set<string>>(new Set());
  const [publishingBatches, setPublishingBatches] = useState<Set<string>>(new Set());
  const [selectedProducts, setSelectedProducts] = useState<Record<string, NormalizedProduct | null>>({});

  const partNumberCount = partNumbersText
    .split(/[,;\n\r]+/)
    .filter(pn => pn.trim().length > 0).length;

  const handleBulkSearch = async () => {
    if (partNumbersText.trim()) {
      await onStartBulkSearch(partNumbersText);
      setPartNumbersText('');
    }
  };

  const handleLoadBatchProducts = async (batchId: string) => {
    setLoadingBatches(prev => new Set(prev).add(batchId));
    try {
      const products = await onLoadBatchProducts();
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
        return <CheckCircle2 className="h-4 w-4 text-success" />;
      case 'failed':
        return <AlertCircle className="h-4 w-4 text-destructive" />;
      case 'cancelled':
        return <XCircle className="h-4 w-4 text-muted-foreground" />;
      default:
        return null;
    }
  };

  const getStatusColor = (status: BatchStatus) => {
    switch (status) {
      case 'pending':
        return 'text-muted-foreground';
      case 'processing':
        return 'text-primary';
      case 'completed':
        return 'text-success';
      case 'failed':
        return 'text-destructive';
      case 'cancelled':
        return 'text-muted-foreground';
      default:
        return '';
    }
  };

  const formatTime = (dateString: string) => {
    return new Date(dateString).toLocaleTimeString();
  };

  const activeBatchesCount = activeBatches.filter(
    b => b.status === 'pending' || b.status === 'processing'
  ).length;

  return (
    <div className="border-b border-border bg-card">
      <Collapsible open={isExpanded} onOpenChange={setIsExpanded}>
        <div className="px-6 py-3 flex items-center justify-between">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" className="p-0 h-auto hover:bg-transparent">
              <div className="flex items-center gap-2">
                {isExpanded ? (
                  <ChevronUp className="h-4 w-4" />
                ) : (
                  <ChevronDown className="h-4 w-4" />
                )}
                <h3 className="font-semibold text-sm">Bulk Operations</h3>
                {activeBatchesCount > 0 && (
                  <span className="bg-primary text-primary-foreground text-xs px-2 py-0.5 rounded-full">
                    {activeBatchesCount} active
                  </span>
                )}
              </div>
            </Button>
          </CollapsibleTrigger>
          <Button
            variant="ghost"
            size="sm"
            onClick={onRefresh}
            className="h-8"
          >
            <RefreshCw className="h-3 w-3 mr-1" />
            Refresh
          </Button>
        </div>

        <CollapsibleContent>
          <div className="px-6 pb-4 space-y-4">
            {/* Error Alert */}
            {error && (
              <div className="bg-destructive/10 border border-destructive/20 rounded-md p-3 flex items-start justify-between">
                <div className="flex items-start gap-2">
                  <AlertCircle className="h-4 w-4 text-destructive mt-0.5" />
                  <span className="text-sm text-destructive">{error}</span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onClearError}
                  className="h-6 w-6 p-0"
                >
                  <X className="h-3 w-3" />
                </Button>
              </div>
            )}

            {/* Input Section */}
            <div className="flex gap-4 items-end">
              <div className="flex-1">
                <Textarea
                  placeholder="Enter part numbers (comma, semicolon, or newline separated)&#10;Example: PN-001, PN-002, PN-003&#10;Or paste from spreadsheet..."
                  value={partNumbersText}
                  onChange={e => setPartNumbersText(e.target.value)}
                  className="min-h-[80px] font-mono text-sm"
                />
                {partNumberCount > 0 && (
                  <p className="text-xs text-muted-foreground mt-1">
                    {partNumberCount} part number{partNumberCount !== 1 ? 's' : ''} detected
                  </p>
                )}
              </div>

              <Button
                onClick={handleBulkSearch}
                disabled={isStarting || partNumberCount === 0}
                className="h-10"
              >
                {isStarting ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Search className="mr-2 h-4 w-4" />
                )}
                Search
              </Button>
            </div>

            {/* Active Batches */}
            {activeBatches.length > 0 && (
              <div className="space-y-2">
                <h4 className="text-sm font-medium">Recent Batches</h4>
                <div className="space-y-2 max-h-[300px] overflow-y-auto">
                  {activeBatches.slice(0, 10).map(batch => (
                    <div
                      key={batch.id}
                      className="border border-border rounded-md p-3 bg-background"
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          {getStatusIcon(batch.status)}
                          <span className="text-sm font-medium capitalize">
                            {batch.batch_type}
                          </span>
                          <span className={`text-xs ${getStatusColor(batch.status)}`}>
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
                              className="h-6 px-2 text-xs"
                            >
                              Cancel
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => toggleBatchExpand(batch.id)}
                            className="h-6 w-6 p-0"
                          >
                            {expandedBatches.has(batch.id) ? (
                              <ChevronUp className="h-3 w-3" />
                            ) : (
                              <ChevronDown className="h-3 w-3" />
                            )}
                          </Button>
                        </div>
                      </div>

                      {/* Progress Bar */}
                      <div className="mb-2">
                        <Progress value={batch.progress_percent} className="h-2" />
                      </div>

                      {/* Progress Stats */}
                      <div className="flex items-center gap-4 text-xs text-muted-foreground">
                        <span>Total: {batch.total_items}</span>
                        {batch.batch_type === 'extract' ? (
                          <>
                            <span>Extracted: {batch.extracted_count}</span>
                            <span>Normalized: {batch.normalized_count}</span>
                          </>
                        ) : (
                          <span>Published: {batch.published_count}</span>
                        )}
                        {batch.failed_count > 0 && (
                          <span className="text-destructive">
                            Failed: {batch.failed_count}
                          </span>
                        )}
                        <span className="ml-auto font-medium">
                          {batch.progress_percent.toFixed(1)}%
                        </span>
                      </div>

                      {/* Action Buttons for completed batches with products */}
                      {batch.status === 'completed' && batch.normalized_count > 0 && (
                        <div className="flex items-center gap-2 mt-3 pt-3 border-t border-border">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleLoadBatchProducts(batch.id)}
                            disabled={loadingBatches.has(batch.id)}
                            className="h-7 text-xs"
                          >
                            {loadingBatches.has(batch.id) ? (
                              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                            ) : (
                              <Download className="h-3 w-3 mr-1" />
                            )}
                            Load Products ({batch.normalized_count})
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleClearBatchProducts(batch.id)}
                            disabled={!batchProducts[batch.id] || batchProducts[batch.id].length === 0}
                            className="h-7 text-xs"
                          >
                            <Trash2 className="h-3 w-3 mr-1" />
                            Clear Table
                          </Button>
                          {batchProducts[batch.id] && batchProducts[batch.id].length > 0 && (
                            <Button
                              variant="default"
                              size="sm"
                              onClick={() => handleBulkPublishBatch(batch.id)}
                              disabled={publishingBatches.has(batch.id) || batchProducts[batch.id].filter(p => p.status !== 'published').length === 0}
                              className="h-7 text-xs"
                            >
                              {publishingBatches.has(batch.id) ? (
                                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                              ) : (
                                <Upload className="h-3 w-3 mr-1" />
                              )}
                              Bulk Publish ({batchProducts[batch.id].filter(p => p.status !== 'published').length})
                            </Button>
                          )}
                        </div>
                      )}

                      {/* Products Table for this batch */}
                      {batchProducts[batch.id] && batchProducts[batch.id].length > 0 && (
                        <div className="mt-3 pt-3 border-t border-border">
                          <div className="rounded-lg border border-border bg-background">
                            <ProductTable
                              products={batchProducts[batch.id]}
                              selectedProduct={selectedProducts[batch.id] || null}
                              actionLoading={actionLoading}
                              onSelectProduct={(product) => handleSelectProduct(batch.id, product)}
                              onEditProduct={onEditProduct}
                              onPublishProduct={onPublishProduct}
                            />
                          </div>
                        </div>
                      )}

                      {/* Expanded Details */}
                      {expandedBatches.has(batch.id) && (
                        <div className="mt-3 pt-3 border-t border-border space-y-2">
                          <div className="text-xs">
                            <span className="text-muted-foreground">Batch ID: </span>
                            <span className="font-mono">{batch.id}</span>
                          </div>
                          {batch.error_message && (
                            <div className="text-xs text-destructive">
                              Error: {batch.error_message}
                            </div>
                          )}
                          {batch.failed_items && batch.failed_items.length > 0 && (
                            <div className="text-xs">
                              <span className="text-muted-foreground">Failed Items:</span>
                              <ul className="mt-1 space-y-1 max-h-[100px] overflow-y-auto">
                                {batch.failed_items.slice(0, 10).map((item, idx) => (
                                  <li key={idx} className="text-destructive font-mono">
                                    {item.part_number}: {item.error}
                                  </li>
                                ))}
                                {batch.failed_items.length > 10 && (
                                  <li className="text-muted-foreground">
                                    ... and {batch.failed_items.length - 10} more
                                  </li>
                                )}
                              </ul>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
