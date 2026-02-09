/**
 * FailedProductsList Component
 *
 * Displays products with sync failures and provides actions to retry or reactivate.
 */

import { useState } from 'react';
import { formatDistanceToNow } from 'date-fns';
import {
  AlertTriangle,
  RefreshCw,
  Play,
  XCircle,
  ChevronDown,
  ChevronUp,
  Clock,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { FailedProduct } from '@/services/syncService';

interface FailedProductsListProps {
  failures: FailedProduct[];
  isLoading: boolean;
  onReactivate: (sku: string) => Promise<boolean>;
  onTriggerSync: (sku: string) => Promise<boolean>;
}

export function FailedProductsList({
  failures,
  isLoading,
  onReactivate,
  onTriggerSync,
}: FailedProductsListProps) {
  const [expandedErrors, setExpandedErrors] = useState<Set<string>>(new Set());
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});

  const toggleError = (sku: string) => {
    setExpandedErrors((prev) => {
      const next = new Set(prev);
      if (next.has(sku)) {
        next.delete(sku);
      } else {
        next.add(sku);
      }
      return next;
    });
  };

  const handleReactivate = async (sku: string) => {
    setActionLoading((prev) => ({ ...prev, [`reactivate-${sku}`]: true }));
    try {
      await onReactivate(sku);
    } finally {
      setActionLoading((prev) => ({ ...prev, [`reactivate-${sku}`]: false }));
    }
  };

  const handleTriggerSync = async (sku: string) => {
    setActionLoading((prev) => ({ ...prev, [`sync-${sku}`]: true }));
    try {
      await onTriggerSync(sku);
    } finally {
      setActionLoading((prev) => ({ ...prev, [`sync-${sku}`]: false }));
    }
  };

  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-500" />
            Failed Products
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="flex items-center gap-4 animate-pulse">
                <div className="h-4 bg-muted rounded w-24" />
                <div className="h-4 bg-muted rounded w-8" />
                <div className="h-4 bg-muted rounded flex-1" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  // Separate critical (deactivated) from recoverable failures
  const criticalFailures = failures.filter((f) => !f.is_active);
  const recoverableFailures = failures.filter((f) => f.is_active);

  if (failures.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-muted-foreground" />
            Failed Products
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-center justify-center py-8 text-center text-muted-foreground">
            <div className="rounded-full bg-emerald-100 dark:bg-emerald-900/30 p-3 mb-3">
              <RefreshCw className="h-6 w-6 text-emerald-600 dark:text-emerald-400" />
            </div>
            <p className="font-medium text-foreground">All products syncing successfully</p>
            <p className="text-sm">No sync failures to report</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-500" />
            Failed Products
          </CardTitle>
          <div className="flex items-center gap-2">
            {criticalFailures.length > 0 && (
              <Badge variant="destructive" className="text-xs">
                {criticalFailures.length} deactivated
              </Badge>
            )}
            <Badge variant="outline" className="text-xs">
              {failures.length} total
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="px-0">
        <ScrollArea className="h-[350px]">
          {/* Critical failures section */}
          {criticalFailures.length > 0 && (
            <div className="mb-4">
              <div className="px-6 py-2 bg-red-50 dark:bg-red-900/10 border-y border-red-100 dark:border-red-900/20">
                <p className="text-xs font-medium text-red-700 dark:text-red-400 flex items-center gap-1">
                  <XCircle className="h-3 w-3" />
                  Deactivated Products (5+ consecutive failures)
                </p>
              </div>
              <div className="divide-y">
                {criticalFailures.map((product) => (
                  <FailedProductRow
                    key={product.sku}
                    product={product}
                    isExpanded={expandedErrors.has(product.sku)}
                    onToggle={() => toggleError(product.sku)}
                    onReactivate={() => handleReactivate(product.sku)}
                    onTriggerSync={() => handleTriggerSync(product.sku)}
                    isReactivating={actionLoading[`reactivate-${product.sku}`]}
                    isSyncing={actionLoading[`sync-${product.sku}`]}
                    isCritical
                  />
                ))}
              </div>
            </div>
          )}

          {/* Recoverable failures section */}
          {recoverableFailures.length > 0 && (
            <div>
              {criticalFailures.length > 0 && (
                <div className="px-6 py-2 bg-amber-50 dark:bg-amber-900/10 border-y border-amber-100 dark:border-amber-900/20">
                  <p className="text-xs font-medium text-amber-700 dark:text-amber-400 flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    Pending Retry ({recoverableFailures.length} products)
                  </p>
                </div>
              )}
              <div className="divide-y">
                {recoverableFailures.map((product) => (
                  <FailedProductRow
                    key={product.sku}
                    product={product}
                    isExpanded={expandedErrors.has(product.sku)}
                    onToggle={() => toggleError(product.sku)}
                    onReactivate={() => handleReactivate(product.sku)}
                    onTriggerSync={() => handleTriggerSync(product.sku)}
                    isReactivating={actionLoading[`reactivate-${product.sku}`]}
                    isSyncing={actionLoading[`sync-${product.sku}`]}
                  />
                ))}
              </div>
            </div>
          )}
        </ScrollArea>
      </CardContent>
    </Card>
  );
}

interface FailedProductRowProps {
  product: FailedProduct;
  isExpanded: boolean;
  onToggle: () => void;
  onReactivate: () => void;
  onTriggerSync: () => void;
  isReactivating?: boolean;
  isSyncing?: boolean;
  isCritical?: boolean;
}

function FailedProductRow({
  product,
  isExpanded,
  onToggle,
  onReactivate,
  onTriggerSync,
  isReactivating,
  isSyncing,
  isCritical,
}: FailedProductRowProps) {
  const formatTime = (timestamp: string | null) => {
    if (!timestamp) return 'Never';
    try {
      return formatDistanceToNow(new Date(timestamp), { addSuffix: true });
    } catch {
      return 'Unknown';
    }
  };

  return (
    <Collapsible open={isExpanded} onOpenChange={onToggle}>
      <div className={cn(
        'px-6 py-3 hover:bg-muted/50 transition-colors',
        isCritical && 'bg-red-50/50 dark:bg-red-900/5'
      )}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 min-w-0">
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0">
                {isExpanded ? (
                  <ChevronUp className="h-4 w-4" />
                ) : (
                  <ChevronDown className="h-4 w-4" />
                )}
              </Button>
            </CollapsibleTrigger>
            <div className="min-w-0">
              <p className="font-mono text-sm truncate">{product.sku}</p>
              <p className="text-xs text-muted-foreground">
                Last attempt: {formatTime(product.last_sync_at)}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <Badge
              variant="secondary"
              className={cn(
                'text-xs',
                product.consecutive_failures >= 5
                  ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                  : product.consecutive_failures >= 3
                    ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
                    : 'bg-muted text-muted-foreground'
              )}
            >
              {product.consecutive_failures} failures
            </Badge>

            <Badge variant="outline" className="text-xs font-mono">
              {product.hour_bucket.toString().padStart(2, '0')}:00
            </Badge>

            <div className="flex items-center gap-1">
              {!product.is_active && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={onReactivate}
                      disabled={isReactivating}
                    >
                      {isReactivating ? (
                        <RefreshCw className="h-3 w-3 animate-spin" />
                      ) : (
                        <Play className="h-3 w-3" />
                      )}
                      <span className="ml-1">Reactivate</span>
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    <p>Reset failure counter and re-enable syncing</p>
                  </TooltipContent>
                </Tooltip>
              )}

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={onTriggerSync}
                    disabled={isSyncing || !product.is_active}
                  >
                    {isSyncing ? (
                      <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <RefreshCw className="h-3.5 w-3.5" />
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>Trigger immediate sync</p>
                </TooltipContent>
              </Tooltip>
            </div>
          </div>
        </div>

        <CollapsibleContent>
          {product.last_error && (
            <div className="mt-3 ml-9 p-3 rounded-md bg-muted/50 border">
              <p className="text-xs font-medium text-muted-foreground mb-1">
                Error Message:
              </p>
              <p className="text-sm text-red-600 dark:text-red-400 font-mono whitespace-pre-wrap break-all">
                {product.last_error}
              </p>
            </div>
          )}
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

/**
 * Compact failures summary for overview cards
 */
export function FailedProductsSummary({
  failures,
  onViewAll,
}: {
  failures: FailedProduct[];
  onViewAll?: () => void;
}) {
  const critical = failures.filter((f) => !f.is_active).length;
  const recoverable = failures.filter((f) => f.is_active).length;

  if (failures.length === 0) {
    return (
      <div className="flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400">
        <RefreshCw className="h-4 w-4" />
        <span>All products healthy</span>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm">
          <AlertTriangle className="h-4 w-4 text-amber-500" />
          <span>{failures.length} products with failures</span>
        </div>
        {onViewAll && (
          <Button variant="link" size="sm" className="h-auto p-0" onClick={onViewAll}>
            View all
          </Button>
        )}
      </div>
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        {critical > 0 && (
          <span className="text-red-600 dark:text-red-400">
            {critical} deactivated
          </span>
        )}
        {recoverable > 0 && (
          <span className="text-amber-600 dark:text-amber-400">
            {recoverable} pending retry
          </span>
        )}
      </div>
    </div>
  );
}
