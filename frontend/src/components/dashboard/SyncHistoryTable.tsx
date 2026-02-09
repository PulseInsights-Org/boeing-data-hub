/**
 * SyncHistoryTable Component
 *
 * Displays recent sync history with product details and status.
 */

import { formatDistanceToNow } from 'date-fns';
import {
  CheckCircle2,
  XCircle,
  Clock,
  RefreshCw,
  DollarSign,
  Package,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
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
import { cn } from '@/lib/utils';
import { SyncHistoryItem } from '@/services/syncService';

interface SyncHistoryTableProps {
  history: SyncHistoryItem[];
  isLoading: boolean;
}

export function SyncHistoryTable({ history, isLoading }: SyncHistoryTableProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Recent Sync Activity</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="flex items-center gap-4 animate-pulse">
                <div className="h-4 bg-muted rounded w-24" />
                <div className="h-4 bg-muted rounded w-16" />
                <div className="h-4 bg-muted rounded w-20" />
                <div className="h-4 bg-muted rounded flex-1" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (history.length === 0) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Recent Sync Activity</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-center justify-center py-8 text-center text-muted-foreground">
            <Clock className="h-8 w-8 mb-2 opacity-50" />
            <p>No sync activity in the last 24 hours</p>
            <p className="text-sm">Products will appear here after they are synced</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">Recent Sync Activity</CardTitle>
          <Badge variant="outline" className="text-xs">
            {history.length} syncs in 24h
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="px-0">
        <ScrollArea className="h-[400px]">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-6">Part Number</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Price</TableHead>
                <TableHead>Qty</TableHead>
                <TableHead>Stock</TableHead>
                <TableHead>Hour</TableHead>
                <TableHead className="pr-6">Synced</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {history.map((item, index) => (
                <SyncHistoryRow key={`${item.sku}-${index}`} item={item} />
              ))}
            </TableBody>
          </Table>
        </ScrollArea>
      </CardContent>
    </Card>
  );
}

function SyncHistoryRow({ item }: { item: SyncHistoryItem }) {
  const statusConfig = {
    success: {
      icon: CheckCircle2,
      color: 'text-emerald-600 dark:text-emerald-400',
      bgColor: 'bg-emerald-100 dark:bg-emerald-900/30',
      label: 'Success',
    },
    failed: {
      icon: XCircle,
      color: 'text-red-600 dark:text-red-400',
      bgColor: 'bg-red-100 dark:bg-red-900/30',
      label: 'Failed',
    },
    syncing: {
      icon: RefreshCw,
      color: 'text-blue-600 dark:text-blue-400',
      bgColor: 'bg-blue-100 dark:bg-blue-900/30',
      label: 'Syncing',
    },
    pending: {
      icon: Clock,
      color: 'text-amber-600 dark:text-amber-400',
      bgColor: 'bg-amber-100 dark:bg-amber-900/30',
      label: 'Pending',
    },
  };

  const status = statusConfig[item.sync_status as keyof typeof statusConfig] || statusConfig.pending;
  const StatusIcon = status.icon;

  const formatPrice = (price: number | null) => {
    if (price === null || price === undefined) return '—';
    return `$${price.toFixed(2)}`;
  };

  const formatTime = (timestamp: string | null) => {
    if (!timestamp) return '—';
    try {
      return formatDistanceToNow(new Date(timestamp), { addSuffix: true });
    } catch {
      return '—';
    }
  };

  return (
    <TableRow className="group">
      <TableCell className="pl-6 font-mono text-sm">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="cursor-default">{item.sku}</span>
          </TooltipTrigger>
          <TooltipContent>
            <span>Click to view sync details</span>
          </TooltipContent>
        </Tooltip>
      </TableCell>
      <TableCell>
        <Badge
          variant="secondary"
          className={cn('gap-1', status.bgColor, status.color)}
        >
          <StatusIcon className={cn('h-3 w-3', item.sync_status === 'syncing' && 'animate-spin')} />
          {status.label}
        </Badge>
      </TableCell>
      <TableCell className="font-mono text-sm">
        <div className="flex items-center gap-1">
          <DollarSign className="h-3 w-3 text-muted-foreground" />
          {formatPrice(item.last_price)}
        </div>
      </TableCell>
      <TableCell className="font-mono text-sm">
        <div className="flex items-center gap-1">
          <Package className="h-3 w-3 text-muted-foreground" />
          {item.last_quantity ?? '—'}
        </div>
      </TableCell>
      <TableCell>
        {item.last_inventory_status ? (
          <Badge
            variant="secondary"
            className={cn(
              'text-xs',
              item.last_inventory_status === 'in_stock'
                ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400'
                : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
            )}
          >
            {item.last_inventory_status === 'in_stock' ? 'In Stock' : 'Out of Stock'}
          </Badge>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell>
        <Badge variant="outline" className="text-xs font-mono">
          {item.hour_bucket.toString().padStart(2, '0')}:00
        </Badge>
      </TableCell>
      <TableCell className="pr-6 text-sm text-muted-foreground">
        {formatTime(item.last_sync_at)}
      </TableCell>
    </TableRow>
  );
}

/**
 * Compact sync history list for sidebar or summary
 */
export function SyncHistoryCompact({ history }: { history: SyncHistoryItem[] }) {
  if (history.length === 0) {
    return (
      <div className="text-center py-4 text-muted-foreground text-sm">
        No recent activity
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {history.slice(0, 10).map((item, index) => {
        const isSuccess = item.sync_status === 'success';
        const isFailed = item.sync_status === 'failed';

        return (
          <div
            key={`${item.sku}-${index}`}
            className="flex items-center justify-between py-1.5 text-sm"
          >
            <div className="flex items-center gap-2">
              {isSuccess ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
              ) : isFailed ? (
                <XCircle className="h-3.5 w-3.5 text-red-500" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5 text-blue-500 animate-spin" />
              )}
              <span className="font-mono truncate max-w-[120px]">{item.sku}</span>
            </div>
            <span className="text-xs text-muted-foreground">
              {item.last_sync_at
                ? formatDistanceToNow(new Date(item.last_sync_at), { addSuffix: true })
                : '—'}
            </span>
          </div>
        );
      })}
    </div>
  );
}
