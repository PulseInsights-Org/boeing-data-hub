import { useState } from 'react';
import { Search, ShoppingBag } from 'lucide-react';
import { Header } from '@/components/dashboard/Header';
import { EditProductModal } from '@/components/dashboard/EditProductModal';
import { ErrorAlert } from '@/components/dashboard/ErrorAlert';
import { SearchPanel } from '@/components/dashboard/SearchPanel';
import { PublishedProductsPanel } from '@/components/dashboard/PublishedProductsPanel';
import { useProducts } from '@/hooks/useProducts';
import { useBulkOperations } from '@/hooks/useBulkOperations';
import { usePublishedProducts } from '@/hooks/usePublishedProducts';
import { NormalizedProduct, ProductStatus } from '@/types/product';
import { getStagingProducts, StagingProduct } from '@/services/bulkService';
import { cn } from '@/lib/utils';

type Tab = 'search' | 'published';

const Index = () => {
  const [activeTab, setActiveTab] = useState<Tab>('search');

  const {
    error,
    actionLoading,
    updateProduct,
    publishProduct,
    clearError,
  } = useProducts();

  const {
    activeBatches,
    isStarting: isBulkStarting,
    error: bulkError,
    statusFilter,
    startBulkSearchOperation,
    startBulkPublishOperation,
    cancelBatchOperation,
    refreshBatches,
    setStatusFilter,
    clearError: clearBulkError,
  } = useBulkOperations();

  const {
    products: publishedProducts,
    total: publishedTotal,
    isLoading: isLoadingPublished,
    error: publishedError,
    searchQuery,
    setSearchQuery,
    refresh: refreshPublished,
    loadMore: loadMorePublished,
    hasMore: hasMorePublished,
  } = usePublishedProducts();

  const [editingProduct, setEditingProduct] = useState<NormalizedProduct | null>(null);

  const handleEditProduct = (product: NormalizedProduct) => {
    setEditingProduct(product);
  };

  const handleSaveProduct = async (product: NormalizedProduct) => {
    await updateProduct(product);
    setEditingProduct(null);
  };

  const handleCloseModal = () => {
    setEditingProduct(null);
  };

  const handleBulkSearch = async (partNumbers: string) => {
    await startBulkSearchOperation(partNumbers);
  };

  const handleCancelBatch = async (batchId: string) => {
    await cancelBatchOperation(batchId);
  };

  // Load products for a specific batch
  const handleLoadBatchProducts = async (batchId: string): Promise<NormalizedProduct[]> => {
    // Pass batchId to filter products that belong to this specific batch
    const response = await getStagingProducts(100, 0, undefined, batchId);
    const now = new Date().toISOString();

    // Convert staging products to NormalizedProduct format
    const normalizedProducts: NormalizedProduct[] = response.products.map((p: StagingProduct) => ({
      id: p.id || p.sku,
      partNumber: p.sku || p.id || '',
      sku: p.sku,
      name: p.boeing_name || p.title || p.sku || '',
      title: p.title || p.boeing_name || p.sku || '',
      description: p.boeing_description || p.body_html || '',
      manufacturer: p.supplier_name || p.vendor || '',
      vendor: p.vendor,
      supplier_name: p.supplier_name,
      length: p.dim_length,
      width: p.dim_width,
      height: p.dim_height,
      dim_length: p.dim_length,
      dim_width: p.dim_width,
      dim_height: p.dim_height,
      dimensionUom: p.dim_uom || '',
      dim_uom: p.dim_uom,
      weight: p.weight,
      weightUnit: p.weight_unit || '',
      weight_uom: p.weight_unit,
      price: p.cost_per_item ?? p.net_price ?? p.price ?? null,
      cost_per_item: p.cost_per_item,
      net_price: p.net_price,
      list_price: p.list_price,
      currency: p.currency ?? 'USD',
      inventory: p.inventory_quantity,
      inventory_quantity: p.inventory_quantity,
      availability: p.inventory_status,
      inventory_status: p.inventory_status,
      status: (p.status as ProductStatus) || 'fetched',
      lastModified: p.updated_at || now,
      country_of_origin: p.country_of_origin,
      base_uom: p.base_uom,
      hazmat_code: p.hazmat_code,
      faa_approval_code: p.faa_approval_code,
      eccn: p.eccn,
      schedule_b_code: p.schedule_b_code,
      condition: p.condition,
      pma: p.pma,
      product_image: p.boeing_image_url || p.image_url,
      thumbnail_image: p.boeing_thumbnail_url,
      distrSrc: 'BDI',
      pnAUrl: '',
      rawBoeingData: {},
    }));

    return normalizedProducts;
  };

  // Clear products for a specific batch (handled in SearchPanel state)
  const handleClearBatchProducts = () => {
    // Products are managed in SearchPanel state
  };

  // Bulk publish products from a batch
  const handleBulkPublishBatch = async (_batchId: string, products: NormalizedProduct[]) => {
    const unpublishedProducts = products.filter(p => p.status !== 'published');
    if (unpublishedProducts.length === 0) return;

    const partNumbers = unpublishedProducts
      .map(p => p.partNumber || p.sku || p.id)
      .filter(Boolean)
      .join(',');

    if (partNumbers) {
      await startBulkPublishOperation(partNumbers);
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <Header />

      {error && (
        <div className="px-6 pt-4">
          <ErrorAlert message={error} onDismiss={clearError} />
        </div>
      )}

      {/* Tab Navigation */}
      <div className="border-b border-border bg-card">
        <div className="px-6">
          <nav className="flex gap-1" aria-label="Tabs">
            <button
              onClick={() => setActiveTab('search')}
              className={cn(
                "flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors",
                activeTab === 'search'
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
              )}
            >
              <Search className="h-4 w-4" />
              Search & Process
            </button>
            <button
              onClick={() => setActiveTab('published')}
              className={cn(
                "flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors",
                activeTab === 'published'
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
              )}
            >
              <ShoppingBag className="h-4 w-4" />
              Published Products
              {publishedTotal > 0 && (
                <span className="bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400 text-xs px-2 py-0.5 rounded-full">
                  {publishedTotal}
                </span>
              )}
            </button>
          </nav>
        </div>
      </div>

      {/* Tab Content */}
      {activeTab === 'search' && (
        <SearchPanel
          activeBatches={activeBatches}
          isStarting={isBulkStarting}
          error={bulkError}
          statusFilter={statusFilter}
          onStartSearch={handleBulkSearch}
          onCancelBatch={handleCancelBatch}
          onRefresh={refreshBatches}
          onClearError={clearBulkError}
          onSetStatusFilter={setStatusFilter}
          onLoadBatchProducts={handleLoadBatchProducts}
          onClearBatchProducts={handleClearBatchProducts}
          onBulkPublishBatch={handleBulkPublishBatch}
          onEditProduct={handleEditProduct}
          onPublishProduct={publishProduct}
          actionLoading={actionLoading}
        />
      )}

      {activeTab === 'published' && (
        <PublishedProductsPanel
          products={publishedProducts}
          total={publishedTotal}
          isLoading={isLoadingPublished}
          error={publishedError}
          searchQuery={searchQuery}
          hasMore={hasMorePublished}
          onSearchChange={setSearchQuery}
          onRefresh={refreshPublished}
          onLoadMore={loadMorePublished}
        />
      )}

      <EditProductModal
        product={editingProduct}
        isOpen={!!editingProduct}
        isSaving={editingProduct ? !!actionLoading[`save-${editingProduct.id}`] : false}
        onClose={handleCloseModal}
        onSave={handleSaveProduct}
      />
    </div>
  );
};

export default Index;
