import { useState } from 'react';
import { Header } from '@/components/dashboard/Header';
import { Toolbar } from '@/components/dashboard/Toolbar';
import { ProductTable } from '@/components/dashboard/ProductTable';
import { EditProductModal } from '@/components/dashboard/EditProductModal';
import { ErrorAlert } from '@/components/dashboard/ErrorAlert';
import { useProducts } from '@/hooks/useProducts';
import { NormalizedProduct } from '@/types/product';

const Index = () => {
  const {
    products,
    selectedProduct,
    isLoading,
    error,
    actionLoading,
    selectProduct,
    fetchProducts,
    updateProduct,
    publishProduct,
    clearError,
  } = useProducts();

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

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <Header />
      
      <div className="flex-1 flex flex-col">
        <Toolbar
          selectedProduct={selectedProduct}
          isLoading={isLoading}
          onFetchProducts={fetchProducts}
        />

        <main className="flex-1 px-6 py-6">
          {error && (
            <div className="mb-4">
              <ErrorAlert message={error} onDismiss={clearError} />
            </div>
          )}

          <div className="rounded-lg border border-border bg-card shadow-card">
            <div className="border-b border-border px-4 py-3">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-base font-semibold text-foreground">Products</h2>
                  <p className="text-sm text-muted-foreground">
                    {products.length === 0
                      ? 'No products loaded'
                      : `${products.length} product${products.length === 1 ? '' : 's'} loaded`}
                  </p>
                </div>
                <div className="flex items-center gap-4 text-sm text-muted-foreground">
                  <div className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-muted-foreground" />
                    <span>Fetched</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-warning" />
                    <span>Enriched</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-full bg-success" />
                    <span>Published</span>
                  </div>
                </div>
              </div>
            </div>

            <ProductTable
              products={products}
              selectedProduct={selectedProduct}
              actionLoading={actionLoading}
              onSelectProduct={selectProduct}
              onEditProduct={handleEditProduct}
              onPublishProduct={publishProduct}
            />
          </div>
        </main>

        {/* Footer Stats */}
        <footer className="border-t border-border bg-card px-6 py-3">
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <div className="flex items-center gap-6">
              <span>
                Total: <span className="font-medium text-foreground">{products.length}</span>
              </span>
              <span>
                Enriched: <span className="font-medium text-foreground">
                  {products.filter(p => p.status === 'enriched' || p.status === 'published').length}
                </span>
              </span>
              <span>
                Published: <span className="font-medium text-foreground">
                  {products.filter(p => p.status === 'published').length}
                </span>
              </span>
            </div>
            <span>Boeing Commerce Connect API</span>
          </div>
        </footer>
      </div>

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
