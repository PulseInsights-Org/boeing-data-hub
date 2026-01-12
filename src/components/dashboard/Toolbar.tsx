import { useState } from 'react';
import { Search, DollarSign, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NormalizedProduct } from '@/types/product';

interface ToolbarProps {
  selectedProduct: NormalizedProduct | null;
  isLoading: boolean;
  actionLoading: { [key: string]: boolean };
  onFetchProducts: (query: string) => Promise<void>;
  onEnrichProduct: (productId: string) => Promise<void>;
}

export function Toolbar({
  selectedProduct,
  isLoading,
  actionLoading,
  onFetchProducts,
  onEnrichProduct,
}: ToolbarProps) {
  const [searchQuery, setSearchQuery] = useState('');

  const handleSearch = () => {
    if (searchQuery.trim()) {
      onFetchProducts(searchQuery);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch();
    }
  };

  const isEnriching = selectedProduct && actionLoading[`enrich-${selectedProduct.id}`];
  const canEnrich = selectedProduct && selectedProduct.status === 'fetched';

  return (
    <div className="border-b border-border bg-card px-6 py-4">
      <div className="flex flex-wrap items-center gap-4">
        {/* Search Section */}
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="text"
              placeholder="Search parts (e.g., bolt, washer)"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              className="w-64 pl-9"
            />
          </div>
          <Button
            onClick={handleSearch}
            disabled={isLoading || !searchQuery.trim()}
            className="min-w-[100px]"
          >
            {isLoading ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Fetching...
              </>
            ) : (
              <>
                <Search className="mr-2 h-4 w-4" />
                Fetch Part
              </>
            )}
          </Button>
        </div>

        {/* Divider */}
        <div className="h-8 w-px bg-border" />

        {/* Enrichment Section */}
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            onClick={() => selectedProduct && onEnrichProduct(selectedProduct.id)}
            disabled={!canEnrich || !!isEnriching}
          >
            {isEnriching ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Getting Price...
              </>
            ) : (
              <>
                <DollarSign className="mr-2 h-4 w-4" />
                Get Price
              </>
            )}
          </Button>
          {selectedProduct && (
            <span className="text-sm text-muted-foreground">
              Selected: <span className="font-medium text-foreground">{selectedProduct.partNumber}</span>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
