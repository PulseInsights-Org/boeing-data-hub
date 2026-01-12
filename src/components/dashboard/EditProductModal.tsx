import { useState, useEffect } from 'react';
import { X, Save, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { NormalizedProduct } from '@/types/product';

interface EditProductModalProps {
  product: NormalizedProduct | null;
  isOpen: boolean;
  isSaving: boolean;
  onClose: () => void;
  onSave: (product: NormalizedProduct) => Promise<void>;
}

export function EditProductModal({
  product,
  isOpen,
  isSaving,
  onClose,
  onSave,
}: EditProductModalProps) {
  const [formData, setFormData] = useState<NormalizedProduct | null>(null);

  useEffect(() => {
    if (product) {
      setFormData({ ...product });
    }
  }, [product]);

  if (!formData) return null;

  const handleChange = (field: keyof NormalizedProduct, value: string | number | null) => {
    setFormData(prev => prev ? { ...prev, [field]: value } : null);
  };

  const handleNumberChange = (field: keyof NormalizedProduct, value: string) => {
    const numValue = value === '' ? null : parseFloat(value);
    handleChange(field, numValue);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (formData) {
      await onSave(formData);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-[600px] max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            Edit Product
            <span className="text-sm font-mono text-muted-foreground">
              {formData.partNumber}
            </span>
          </DialogTitle>
          <DialogDescription>
            Edit product details before publishing to Shopify. Changes will be saved to the normalized products database.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-6 py-4">
          {/* Basic Information */}
          <div className="space-y-4">
            <h3 className="text-sm font-semibold text-foreground">Basic Information</h3>
            
            <div className="grid gap-4">
              <div className="space-y-2">
                <Label htmlFor="title">Product Title</Label>
                <Input
                  id="title"
                  value={formData.title}
                  onChange={(e) => handleChange('title', e.target.value)}
                  placeholder="Enter product title"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="description">Description</Label>
                <Textarea
                  id="description"
                  value={formData.description}
                  onChange={(e) => handleChange('description', e.target.value)}
                  placeholder="Enter product description"
                  rows={3}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="manufacturer">Manufacturer</Label>
                <Input
                  id="manufacturer"
                  value={formData.manufacturer}
                  onChange={(e) => handleChange('manufacturer', e.target.value)}
                  placeholder="Enter manufacturer"
                />
              </div>
            </div>
          </div>

          {/* Dimensions */}
          <div className="space-y-4">
            <h3 className="text-sm font-semibold text-foreground">Dimensions</h3>
            
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-2">
                <Label htmlFor="length">Length ({formData.dimensionUom})</Label>
                <Input
                  id="length"
                  type="number"
                  step="0.01"
                  value={formData.length ?? ''}
                  onChange={(e) => handleNumberChange('length', e.target.value)}
                  placeholder="—"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="width">Width ({formData.dimensionUom})</Label>
                <Input
                  id="width"
                  type="number"
                  step="0.01"
                  value={formData.width ?? ''}
                  onChange={(e) => handleNumberChange('width', e.target.value)}
                  placeholder="—"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="height">Height ({formData.dimensionUom})</Label>
                <Input
                  id="height"
                  type="number"
                  step="0.01"
                  value={formData.height ?? ''}
                  onChange={(e) => handleNumberChange('height', e.target.value)}
                  placeholder="—"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="weight">Weight ({formData.weightUnit})</Label>
                <Input
                  id="weight"
                  type="number"
                  step="0.01"
                  value={formData.weight ?? ''}
                  onChange={(e) => handleNumberChange('weight', e.target.value)}
                  placeholder="—"
                />
              </div>
            </div>
          </div>

          {/* Pricing & Inventory */}
          <div className="space-y-4">
            <h3 className="text-sm font-semibold text-foreground">Pricing & Inventory</h3>
            
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="price">Price (USD)</Label>
                <Input
                  id="price"
                  type="number"
                  step="0.01"
                  value={formData.price ?? ''}
                  onChange={(e) => handleNumberChange('price', e.target.value)}
                  placeholder="0.00"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="inventory">Inventory Count</Label>
                <Input
                  id="inventory"
                  type="number"
                  step="1"
                  value={formData.inventory ?? ''}
                  onChange={(e) => handleNumberChange('inventory', e.target.value)}
                  placeholder="0"
                />
              </div>
            </div>
          </div>

          {/* Raw Data Preview */}
          <div className="space-y-2">
            <Label className="text-muted-foreground">Raw Boeing Data (read-only)</Label>
            <div className="bg-muted rounded-md p-3 max-h-32 overflow-y-auto">
              <pre className="text-xs font-mono text-muted-foreground whitespace-pre-wrap">
                {JSON.stringify(formData.rawBoeingData, null, 2)}
              </pre>
            </div>
          </div>
        </form>

        <DialogFooter className="gap-2 sm:gap-0">
          <Button type="button" variant="outline" onClick={onClose} disabled={isSaving}>
            <X className="mr-2 h-4 w-4" />
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={isSaving}>
            {isSaving ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="mr-2 h-4 w-4" />
                Save Changes
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
