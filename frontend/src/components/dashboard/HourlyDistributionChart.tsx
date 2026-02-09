/**
 * HourlyDistributionChart Component
 *
 * Visual representation of product distribution across time buckets.
 * Shows which hours/buckets have products scheduled for sync.
 */

import { useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { HourlyStats, SyncDashboardData } from '@/services/syncService';
import { Clock, CheckCircle2, AlertCircle, Loader2, Circle } from 'lucide-react';

interface HourlyDistributionChartProps {
  hourlyStats: HourlyStats[];
  currentHour: number;
  dashboard: SyncDashboardData | null;
  isLoading: boolean;
}

export function HourlyDistributionChart({
  hourlyStats,
  currentHour,
  dashboard,
  isLoading,
}: HourlyDistributionChartProps) {
  // Calculate max count for scaling
  const maxCount = useMemo(() => {
    if (!hourlyStats.length) return 1;
    return Math.max(...hourlyStats.map(h => h.total), 1);
  }, [hourlyStats]);

  // Format hour label based on sync mode
  const formatHourLabel = (hour: number) => {
    if (dashboard?.sync_mode === 'testing') {
      return `B${hour}`;
    }
    return `${hour.toString().padStart(2, '0')}:00`;
  };

  const formatHourShort = (hour: number) => {
    if (dashboard?.sync_mode === 'testing') {
      return hour.toString();
    }
    return hour.toString().padStart(2, '0');
  };

  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            {dashboard?.sync_mode === 'testing' ? 'Bucket Distribution' : 'Hourly Distribution'}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-6 gap-2 animate-pulse">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="h-24 bg-muted rounded-lg" />
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <Clock className="h-4 w-4 text-muted-foreground" />
            {dashboard?.sync_mode === 'testing' ? 'Bucket Distribution' : 'Hourly Distribution'}
          </CardTitle>
          <div className="flex items-center gap-4 text-xs">
            <div className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
              <span className="text-muted-foreground">Success</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full bg-amber-500" />
              <span className="text-muted-foreground">Pending</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full bg-red-500" />
              <span className="text-muted-foreground">Failed</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse" />
              <span className="text-muted-foreground">Syncing</span>
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        {/* Slot Grid */}
        <div className={cn(
          "grid gap-2",
          hourlyStats.length <= 6 ? "grid-cols-6" : "grid-cols-6 md:grid-cols-8 lg:grid-cols-12"
        )}>
          {hourlyStats.map((stat) => (
            <SlotCard
              key={stat.hour}
              stat={stat}
              isCurrentHour={stat.hour === currentHour}
              maxCount={maxCount}
              formatLabel={formatHourShort}
              syncMode={dashboard?.sync_mode || 'production'}
            />
          ))}
        </div>

        {/* Current Processing Indicator */}
        <div className="mt-4 flex items-center justify-center">
          <div className="flex items-center gap-2 px-4 py-2 rounded-full bg-primary/5 border border-primary/20">
            <div className="relative flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75" />
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-primary" />
            </div>
            <span className="text-sm text-muted-foreground">
              Currently processing{' '}
              <span className="font-semibold text-foreground">
                {formatHourLabel(currentHour)}
              </span>
              {' '}with{' '}
              <span className="font-semibold text-foreground">
                {dashboard?.current_hour_products || 0}
              </span>
              {' '}products
            </span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

interface SlotCardProps {
  stat: HourlyStats;
  isCurrentHour: boolean;
  maxCount: number;
  formatLabel: (hour: number) => string;
  syncMode: string;
}

function SlotCard({ stat, isCurrentHour, maxCount, formatLabel, syncMode }: SlotCardProps) {
  const isEmpty = stat.total === 0;
  const hasFailures = stat.failed > 0;
  const hasSyncing = stat.syncing > 0;
  const allSuccess = stat.total > 0 && stat.success === stat.total;

  // Calculate fill percentage for visual indicator
  const fillPercent = maxCount > 0 ? (stat.total / maxCount) * 100 : 0;

  // Determine the dominant status for the card appearance
  const getStatusColor = () => {
    if (isEmpty) return 'bg-muted/30';
    if (hasFailures) return 'bg-red-500/10';
    if (hasSyncing) return 'bg-blue-500/10';
    if (allSuccess) return 'bg-emerald-500/10';
    return 'bg-amber-500/10';
  };

  const getBorderColor = () => {
    if (isCurrentHour) return 'ring-2 ring-primary ring-offset-2';
    if (isEmpty) return 'border-dashed border-muted-foreground/20';
    if (hasFailures) return 'border-red-500/30';
    if (hasSyncing) return 'border-blue-500/50';
    if (allSuccess) return 'border-emerald-500/30';
    return 'border-amber-500/30';
  };

  const getStatusIcon = () => {
    if (isEmpty) return <Circle className="h-3.5 w-3.5 text-muted-foreground/40" />;
    if (hasFailures) return <AlertCircle className="h-3.5 w-3.5 text-red-500" />;
    if (hasSyncing) return <Loader2 className="h-3.5 w-3.5 text-blue-500 animate-spin" />;
    if (allSuccess) return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />;
    return <Clock className="h-3.5 w-3.5 text-amber-500" />;
  };

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          className={cn(
            "relative flex flex-col items-center justify-center p-3 rounded-xl border transition-all cursor-pointer",
            "hover:scale-105 hover:shadow-md",
            getStatusColor(),
            getBorderColor(),
            isCurrentHour && "shadow-lg"
          )}
        >
          {/* Hour Label */}
          <span className={cn(
            "text-xs font-medium mb-1",
            isCurrentHour ? "text-primary" : "text-muted-foreground"
          )}>
            {syncMode === 'testing' ? `B${stat.hour}` : formatLabel(stat.hour)}
          </span>

          {/* Count Display */}
          <span className={cn(
            "text-2xl font-bold tabular-nums",
            isEmpty ? "text-muted-foreground/30" : "text-foreground"
          )}>
            {stat.total}
          </span>

          {/* Status Icon */}
          <div className="mt-1">
            {getStatusIcon()}
          </div>

          {/* Progress Bar (subtle bottom indicator) */}
          {!isEmpty && (
            <div className="absolute bottom-0 left-0 right-0 h-1 rounded-b-xl overflow-hidden bg-muted/30">
              <div
                className={cn(
                  "h-full transition-all duration-500",
                  hasFailures ? "bg-red-500" :
                  hasSyncing ? "bg-blue-500" :
                  allSuccess ? "bg-emerald-500" :
                  "bg-amber-500"
                )}
                style={{ width: `${fillPercent}%` }}
              />
            </div>
          )}

          {/* Current Hour Pulse Effect */}
          {isCurrentHour && (
            <div className="absolute -top-1 -right-1">
              <span className="relative flex h-3 w-3">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75" />
                <span className="relative inline-flex rounded-full h-3 w-3 bg-primary" />
              </span>
            </div>
          )}
        </div>
      </TooltipTrigger>
      <TooltipContent side="top" className="text-xs">
        <div className="space-y-1.5">
          <div className="font-semibold border-b pb-1">
            {syncMode === 'testing'
              ? `Bucket ${stat.hour}`
              : `${stat.hour}:00 - ${stat.hour}:59 UTC`}
            {isCurrentHour && (
              <Badge variant="secondary" className="ml-2 text-[10px] px-1.5">
                Active
              </Badge>
            )}
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
            <span className="text-muted-foreground">Total:</span>
            <span className="font-medium">{stat.total}</span>
            <span className="text-emerald-600 dark:text-emerald-400">Success:</span>
            <span className="font-medium">{stat.success}</span>
            <span className="text-amber-600 dark:text-amber-400">Pending:</span>
            <span className="font-medium">{stat.pending}</span>
            <span className="text-blue-600 dark:text-blue-400">Syncing:</span>
            <span className="font-medium">{stat.syncing}</span>
            <span className="text-red-600 dark:text-red-400">Failed:</span>
            <span className="font-medium">{stat.failed}</span>
          </div>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * Alternative Timeline View - Horizontal scrollable timeline
 */
export function HourlyTimeline({
  hourlyStats,
  currentHour,
  dashboard,
}: {
  hourlyStats: HourlyStats[];
  currentHour: number;
  dashboard: SyncDashboardData | null;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base flex items-center gap-2">
          <Clock className="h-4 w-4 text-muted-foreground" />
          Sync Timeline
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="relative">
          {/* Timeline Line */}
          <div className="absolute top-1/2 left-0 right-0 h-0.5 bg-border -translate-y-1/2" />

          {/* Timeline Nodes */}
          <div className="relative flex justify-between">
            {hourlyStats.map((stat) => {
              const isCurrentHour = stat.hour === currentHour;
              const hasProducts = stat.total > 0;
              const hasFailures = stat.failed > 0;
              const hasSyncing = stat.syncing > 0;

              return (
                <Tooltip key={stat.hour}>
                  <TooltipTrigger asChild>
                    <div className="flex flex-col items-center">
                      {/* Node */}
                      <div
                        className={cn(
                          "w-8 h-8 rounded-full border-2 flex items-center justify-center transition-all cursor-pointer",
                          "hover:scale-110",
                          isCurrentHour && "ring-2 ring-primary ring-offset-2",
                          !hasProducts && "bg-muted border-muted-foreground/20",
                          hasProducts && !hasFailures && !hasSyncing && "bg-emerald-100 border-emerald-500 dark:bg-emerald-900/30",
                          hasFailures && "bg-red-100 border-red-500 dark:bg-red-900/30",
                          hasSyncing && "bg-blue-100 border-blue-500 dark:bg-blue-900/30"
                        )}
                      >
                        <span className={cn(
                          "text-xs font-bold",
                          !hasProducts && "text-muted-foreground",
                          hasProducts && !hasFailures && !hasSyncing && "text-emerald-700 dark:text-emerald-400",
                          hasFailures && "text-red-700 dark:text-red-400",
                          hasSyncing && "text-blue-700 dark:text-blue-400"
                        )}>
                          {stat.total}
                        </span>
                      </div>
                      {/* Label */}
                      <span className={cn(
                        "text-[10px] mt-1",
                        isCurrentHour ? "font-bold text-primary" : "text-muted-foreground"
                      )}>
                        {dashboard?.sync_mode === 'testing' ? `B${stat.hour}` : stat.hour}
                      </span>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent>
                    <div className="text-xs">
                      <div className="font-medium">{stat.total} products</div>
                      <div className="text-muted-foreground">
                        {stat.success} synced, {stat.pending} pending
                      </div>
                    </div>
                  </TooltipContent>
                </Tooltip>
              );
            })}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * Compact version for smaller spaces
 */
export function HourlyDistributionMini({
  hourlyStats,
  currentHour,
}: {
  hourlyStats: HourlyStats[];
  currentHour: number;
}) {
  const maxCount = useMemo(() => {
    if (!hourlyStats.length) return 1;
    return Math.max(...hourlyStats.map(h => h.total), 1);
  }, [hourlyStats]);

  return (
    <div className="flex items-end gap-1 h-10">
      {hourlyStats.map((stat) => {
        const height = maxCount > 0 ? (stat.total / maxCount) * 100 : 0;
        const isCurrentHour = stat.hour === currentHour;

        return (
          <Tooltip key={stat.hour}>
            <TooltipTrigger asChild>
              <div
                className={cn(
                  "flex-1 rounded-sm transition-all cursor-pointer hover:opacity-80",
                  isCurrentHour ? "bg-primary" : "bg-muted-foreground/30",
                  stat.failed > 0 && "bg-red-500",
                  stat.syncing > 0 && "bg-blue-500 animate-pulse"
                )}
                style={{ height: `${Math.max(height, 15)}%` }}
              />
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">
              <span className="font-medium">{stat.total}</span> products
            </TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}
