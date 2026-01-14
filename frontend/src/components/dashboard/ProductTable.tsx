import { Edit, Upload, Loader2, ExternalLink } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { StatusBadge } from './StatusBadge';
import { NormalizedProduct } from '@/types/product';
import { cn } from '@/lib/utils';

interface ProductTableProps {
  products: NormalizedProduct[];
  selectedProduct: NormalizedProduct | null;
  actionLoading: { [key: string]: boolean };
  onSelectProduct: (product: NormalizedProduct | null) => void;
  onEditProduct: (product: NormalizedProduct) => void;
  onPublishProduct: (productId: string) => Promise<{ success: boolean; error?: string }>;
}

function formatDimensions(product: NormalizedProduct): string {
  if (!product.length && !product.width && !product.height) {
    return '—';
  }
  const dims = [product.length, product.width, product.height]
    .map(d => d?.toFixed(1) ?? '—')
    .join(' × ');
  return `${dims} ${product.dimensionUom}`;
}

function formatWeight(product: NormalizedProduct): string {
  if (!product.weight) return '—';
  return `${product.weight.toFixed(2)} ${product.weightUnit}`;
}

function formatPrice(price: number | null): string {
  if (price === null) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
  }).format(price);
}

function formatInventory(inventory: number | null): string {
  if (inventory === null) return '—';
  return inventory.toLocaleString();
}

export function ProductTable({
  products,
  selectedProduct,
  actionLoading,
  onSelectProduct,
  onEditProduct,
  onPublishProduct,
}: ProductTableProps) {
  const handleRowSelect = (product: NormalizedProduct) => {
    if (selectedProduct?.id === product.id) {
      onSelectProduct(null);
    } else {
      onSelectProduct(product);
    }
  };

  if (products.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <div className="rounded-full bg-muted p-4 mb-4">
          <ExternalLink className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-medium text-foreground mb-1">No products loaded</h3>
        <p className="text-sm text-muted-foreground max-w-sm">
          Use the search bar above to fetch products from the Boeing Commerce Connect API.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="w-12"></TableHead>
            <TableHead className="font-semibold">Part Number</TableHead>
            <TableHead className="font-semibold">Name</TableHead>
            <TableHead className="font-semibold">Manufacturer</TableHead>
            <TableHead className="font-semibold">Dimensions</TableHead>
            <TableHead className="font-semibold">Weight</TableHead>
            <TableHead className="font-semibold text-right">Price</TableHead>
            <TableHead className="font-semibold text-right">Inventory</TableHead>
            <TableHead className="font-semibold">Status</TableHead>
            <TableHead className="font-semibold text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {products.map((product) => {
            const isSelected = selectedProduct?.id === product.id;
            const isPublishing = actionLoading[`publish-${product.id}`];
            const canPublish = product.status !== 'published' && (product.price !== null);

            return (
              <TableRow
                key={product.id}
                className={cn(
                  'cursor-pointer transition-colors',
                  isSelected && 'bg-accent'
                )}
                onClick={() => handleRowSelect(product)}
              >
                <TableCell>
                  <Checkbox
                    checked={isSelected}
                    onCheckedChange={() => handleRowSelect(product)}
                    onClick={(e) => e.stopPropagation()}
                  />
                </TableCell>
                <TableCell className="font-mono text-sm font-medium">
                  {product.partNumber}
                </TableCell>
                <TableCell className="max-w-[200px]">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="block truncate">{product.name}</span>
                    </TooltipTrigger>
                    <TooltipContent side="bottom" className="max-w-xs">
                      <p className="font-medium">{product.name}</p>
                      <p className="text-xs text-muted-foreground mt-1">{product.description}</p>
                    </TooltipContent>
                  </Tooltip>
                </TableCell>
                <TableCell className="text-sm">{product.manufacturer}</TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {formatDimensions(product)}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {formatWeight(product)}
                </TableCell>
                <TableCell className={cn(
                  'text-right font-medium',
                  product.price !== null ? 'text-foreground' : 'text-muted-foreground'
                )}>
                  {formatPrice(product.price)}
                </TableCell>
                <TableCell className={cn(
                  'text-right',
                  product.inventory !== null ? 'text-foreground' : 'text-muted-foreground'
                )}>
                  {formatInventory(product.inventory)}
                </TableCell>
                <TableCell>
                  <StatusBadge status={product.status} />
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        onEditProduct(product);
                      }}
                    >
                      <Edit className="h-4 w-4" />
                    </Button>
                    <Button
                      variant={canPublish ? 'default' : 'ghost'}
                      size="sm"
                      disabled={!canPublish || isPublishing}
                      onClick={(e) => {
                        e.stopPropagation();
                        onPublishProduct(product.id);
                      }}
                    >
                      {isPublishing ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Upload className="h-4 w-4" />
                      )}
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
