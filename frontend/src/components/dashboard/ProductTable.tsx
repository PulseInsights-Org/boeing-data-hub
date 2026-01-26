import { useState, useEffect } from 'react';
import { Edit, Upload, Loader2, ExternalLink, FileJson, ChevronDown, ChevronRight } from 'lucide-react';
import { getRawBoeingData } from '@/services/bulkService';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
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
  return `${dims} ${product.dimensionUom || ''}`.trim();
}

function formatWeight(product: NormalizedProduct): string {
  if (!product.weight) return '—';
  return `${product.weight.toFixed(2)} ${product.weightUnit || ''}`.trim();
}

function formatPrice(price: number | null | undefined): string {
  if (price === null || price === undefined) return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
  }).format(price);
}

function formatInventory(inventory: number | null | undefined): string {
  if (inventory === null || inventory === undefined) return '—';
  return inventory.toLocaleString();
}

function ProductExpandedDetails({ product }: { product: NormalizedProduct }) {
  const details = [
    { label: 'Supplier', value: product.supplier_name },
    { label: 'Vendor', value: product.vendor },
    { label: 'SKU', value: product.sku },
    { label: 'List Price', value: product.list_price ? formatPrice(product.list_price) : null },
    { label: 'Net Price', value: product.net_price ? formatPrice(product.net_price) : null },
    { label: 'Currency', value: product.currency },
    { label: 'Quantity', value: product.quantity },
    { label: 'In Stock', value: product.in_stock !== null && product.in_stock !== undefined ? (product.in_stock ? 'Yes' : 'No') : null },
    { label: 'Inventory Status', value: product.inventory_status },
    { label: 'Base UOM', value: product.base_uom },
    { label: 'Country of Origin', value: product.country_of_origin },
    { label: 'FAA Approval', value: product.faa_approval_code },
    { label: 'ECCN', value: product.eccn },
    { label: 'Hazmat Code', value: product.hazmat_code },
    { label: 'Schedule B', value: product.schedule_b_code },
    { label: 'Cert', value: product.cert },
  ].filter(d => d.value !== null && d.value !== undefined && d.value !== '');

  return (
    <div className="px-4 py-3 bg-muted/30 border-t">
      <div className="grid grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3 text-sm">
        {details.map((detail, idx) => (
          <div key={idx}>
            <span className="text-muted-foreground text-xs">{detail.label}</span>
            <p className="font-medium truncate">{detail.value}</p>
          </div>
        ))}
      </div>
      {product.description && (
        <div className="mt-3 text-sm">
          <span className="text-muted-foreground text-xs">Description</span>
          <p className="text-foreground">{product.description}</p>
        </div>
      )}
      {product.location_availabilities && product.location_availabilities.length > 0 && (
        <div className="mt-3 text-sm">
          <span className="text-muted-foreground text-xs">Location Availability</span>
          <p className="text-foreground">
            {product.location_availabilities
              .filter(loc => loc.location)
              .map(loc => `${loc.location}: ${loc.avail_quantity ?? 0}`)
              .join(' | ')}
          </p>
        </div>
      )}
    </div>
  );
}

export function ProductTable({
  products,
  selectedProduct,
  actionLoading,
  onSelectProduct,
  onEditProduct,
  onPublishProduct,
}: ProductTableProps) {
  const [rawDataProduct, setRawDataProduct] = useState<NormalizedProduct | null>(null);
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [fetchedRawData, setFetchedRawData] = useState<Record<string, unknown> | null>(null);
  const [isLoadingRawData, setIsLoadingRawData] = useState(false);

  // Fetch raw data from API when modal opens and product doesn't have local raw data
  useEffect(() => {
    const fetchRawData = async () => {
      if (!rawDataProduct) {
        setFetchedRawData(null);
        return;
      }

      // Check if product already has raw data
      const hasLocalRawData =
        (rawDataProduct.raw_boeing_data && Object.keys(rawDataProduct.raw_boeing_data).length > 0) ||
        (rawDataProduct.rawBoeingData && Object.keys(rawDataProduct.rawBoeingData).length > 0);

      if (hasLocalRawData) {
        setFetchedRawData(null);
        return;
      }

      // Fetch from API
      setIsLoadingRawData(true);
      try {
        const partNumber = rawDataProduct.partNumber || rawDataProduct.sku || rawDataProduct.id;
        const response = await getRawBoeingData(partNumber);
        setFetchedRawData(response.raw_data);
      } catch (error) {
        console.error('Failed to fetch raw Boeing data:', error);
        setFetchedRawData(null);
      } finally {
        setIsLoadingRawData(false);
      }
    };

    fetchRawData();
  }, [rawDataProduct]);

  const toggleExpanded = (productId: string) => {
    setExpandedRows(prev => {
      const next = new Set(prev);
      if (next.has(productId)) {
        next.delete(productId);
      } else {
        next.add(productId);
      }
      return next;
    });
  };

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
    <>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-8"></TableHead>
              <TableHead className="w-10"></TableHead>
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
              const isExpanded = expandedRows.has(product.id);
              // Allow publish if product has price OR net_price OR cost_per_item
              const hasPrice = product.price !== null || product.net_price !== null || product.cost_per_item !== null;
              const canPublish = product.status !== 'published' && hasPrice;

              return (
                <Collapsible key={product.id} open={isExpanded} onOpenChange={() => toggleExpanded(product.id)} asChild>
                  <>
                    <TableRow
                      className={cn(
                        'cursor-pointer transition-colors',
                        isSelected && 'bg-accent',
                        isExpanded && 'border-b-0'
                      )}
                      onClick={() => handleRowSelect(product)}
                    >
                      <TableCell className="p-1">
                        <CollapsibleTrigger asChild onClick={(e) => e.stopPropagation()}>
                          <Button variant="ghost" size="sm" className="h-6 w-6 p-0">
                            {isExpanded ? (
                              <ChevronDown className="h-4 w-4" />
                            ) : (
                              <ChevronRight className="h-4 w-4" />
                            )}
                          </Button>
                        </CollapsibleTrigger>
                      </TableCell>
                      <TableCell className="p-1">
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
                        <span className="block truncate" title={product.name}>
                          {product.name}
                        </span>
                      </TableCell>
                      <TableCell className="text-sm">{product.manufacturer || '—'}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatDimensions(product)}
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground">
                        {formatWeight(product)}
                      </TableCell>
                      <TableCell className={cn(
                        'text-right font-medium',
                        hasPrice ? 'text-foreground' : 'text-muted-foreground'
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
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 px-2 text-xs"
                            onClick={(e) => {
                              e.stopPropagation();
                              setRawDataProduct(product);
                            }}
                            title="View Raw Data"
                          >
                            <FileJson className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 px-2"
                            onClick={(e) => {
                              e.stopPropagation();
                              onEditProduct(product);
                            }}
                            title="Edit"
                          >
                            <Edit className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant={canPublish ? 'default' : 'ghost'}
                            size="sm"
                            className="h-7 px-2"
                            disabled={!canPublish || isPublishing}
                            onClick={(e) => {
                              e.stopPropagation();
                              onPublishProduct(product.id);
                            }}
                            title="Publish to Shopify"
                          >
                            {isPublishing ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Upload className="h-3.5 w-3.5" />
                            )}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                    <CollapsibleContent asChild>
                      <tr>
                        <td colSpan={11} className="p-0">
                          <ProductExpandedDetails product={product} />
                        </td>
                      </tr>
                    </CollapsibleContent>
                  </>
                </Collapsible>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* Raw Data Modal */}
      <Dialog open={!!rawDataProduct} onOpenChange={() => setRawDataProduct(null)}>
        <DialogContent className="max-w-3xl max-h-[80vh]">
          <DialogHeader>
            <DialogTitle>Raw Boeing Data - {rawDataProduct?.partNumber}</DialogTitle>
          </DialogHeader>
          <div className="overflow-auto max-h-[60vh] bg-muted rounded-md p-4">
            {isLoadingRawData ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                <span className="ml-2 text-sm text-muted-foreground">Loading raw data...</span>
              </div>
            ) : (
              <pre className="text-xs whitespace-pre-wrap break-words font-mono">
                {rawDataProduct?.raw_boeing_data && Object.keys(rawDataProduct.raw_boeing_data).length > 0
                  ? JSON.stringify(rawDataProduct.raw_boeing_data, null, 2)
                  : rawDataProduct?.rawBoeingData && Object.keys(rawDataProduct.rawBoeingData).length > 0
                    ? JSON.stringify(rawDataProduct.rawBoeingData, null, 2)
                    : fetchedRawData && Object.keys(fetchedRawData).length > 0
                      ? JSON.stringify(fetchedRawData, null, 2)
                      : 'No raw data available.'}
              </pre>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
