/**
 * SyncStatusCards Component
 *
 * Displays overview statistics cards for the Auto-Sync dashboard:
 * - Total products in sync
 * - Active products
 * - Success rate
 * - Current hour being processed
 * - Failed products count
 */

import {
  RefreshCw,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  Activity,
  Zap,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { SyncDashboardData } from '@/services/syncService';

interface SyncStatusCardsProps {
  dashboard: SyncDashboardData | null;
  isLoading: boolean;
}

export function SyncStatusCards({ dashboard, isLoading }: SyncStatusCardsProps) {
  if (isLoading || !dashboard) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        {[...Array(6)].map((_, i) => (
          <Card key={i} className="animate-pulse">
            <CardContent className="p-4">
              <div className="h-4 bg-muted rounded w-20 mb-2" />
              <div className="h-8 bg-muted rounded w-16" />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  const cards = [
    {
      title: 'Total Products',
      value: dashboard.total_products,
      icon: RefreshCw,
      color: 'text-blue-600 dark:text-blue-400',
      bgColor: 'bg-blue-50 dark:bg-blue-900/20',
      description: 'In sync schedule',
    },
    {
      title: 'Active',
      value: dashboard.active_products,
      icon: Activity,
      color: 'text-emerald-600 dark:text-emerald-400',
      bgColor: 'bg-emerald-50 dark:bg-emerald-900/20',
      description: `${dashboard.inactive_products} inactive`,
    },
    {
      title: 'Success Rate',
      value: `${dashboard.success_rate_percent.toFixed(1)}%`,
      icon: CheckCircle2,
      color: dashboard.success_rate_percent >= 90
        ? 'text-emerald-600 dark:text-emerald-400'
        : dashboard.success_rate_percent >= 70
          ? 'text-amber-600 dark:text-amber-400'
          : 'text-red-600 dark:text-red-400',
      bgColor: dashboard.success_rate_percent >= 90
        ? 'bg-emerald-50 dark:bg-emerald-900/20'
        : dashboard.success_rate_percent >= 70
          ? 'bg-amber-50 dark:bg-amber-900/20'
          : 'bg-red-50 dark:bg-red-900/20',
      description: `${dashboard.status_counts.success} synced today`,
    },
    {
      title: 'Current Hour',
      value: dashboard.sync_mode === 'testing'
        ? `Bucket ${dashboard.current_hour}`
        : `${dashboard.current_hour}:00`,
      icon: Clock,
      color: 'text-purple-600 dark:text-purple-400',
      bgColor: 'bg-purple-50 dark:bg-purple-900/20',
      description: `${dashboard.current_hour_products} products`,
    },
    {
      title: 'Syncing Now',
      value: dashboard.status_counts.syncing,
      icon: Zap,
      color: 'text-cyan-600 dark:text-cyan-400',
      bgColor: 'bg-cyan-50 dark:bg-cyan-900/20',
      description: `${dashboard.status_counts.pending} pending`,
    },
    {
      title: 'Failures',
      value: dashboard.status_counts.failed,
      icon: dashboard.high_failure_count > 0 ? AlertTriangle : XCircle,
      color: dashboard.status_counts.failed > 0
        ? 'text-red-600 dark:text-red-400'
        : 'text-muted-foreground',
      bgColor: dashboard.status_counts.failed > 0
        ? 'bg-red-50 dark:bg-red-900/20'
        : 'bg-muted/50',
      description: dashboard.high_failure_count > 0
        ? `${dashboard.high_failure_count} critical`
        : 'No failures',
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
      {cards.map((card) => (
        <Card key={card.title} className="relative overflow-hidden">
          <CardContent className="p-4">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                  {card.title}
                </p>
                <p className="mt-1 text-2xl font-bold tabular-nums">
                  {card.value}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {card.description}
                </p>
              </div>
              <div className={cn('p-2 rounded-lg', card.bgColor)}>
                <card.icon className={cn('h-5 w-5', card.color)} />
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

/**
 * Sync Mode Indicator
 * Shows current sync mode (production/testing) with configuration
 */
export function SyncModeIndicator({ dashboard }: { dashboard: SyncDashboardData | null }) {
  if (!dashboard) return null;

  const isTestMode = dashboard.sync_mode === 'testing';

  return (
    <div className={cn(
      'flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium',
      isTestMode
        ? 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300'
        : 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300'
    )}>
      <span className={cn(
        'w-2 h-2 rounded-full animate-pulse',
        isTestMode ? 'bg-amber-500' : 'bg-emerald-500'
      )} />
      {isTestMode ? (
        <>Testing Mode ({dashboard.max_buckets} buckets)</>
      ) : (
        <>Production Mode (24-hour cycle)</>
      )}
    </div>
  );
}

/**
 * Efficiency Badge
 * Shows slot efficiency percentage
 */
export function EfficiencyBadge({ dashboard }: { dashboard: SyncDashboardData | null }) {
  if (!dashboard) return null;

  const efficiency = dashboard.efficiency_percent;

  return (
    <div className={cn(
      'flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium',
      efficiency >= 80
        ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300'
        : efficiency >= 50
          ? 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300'
          : 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300'
    )}>
      <Activity className="h-3 w-3" />
      {efficiency.toFixed(0)}% Efficiency
      <span className="text-muted-foreground">
        ({dashboard.active_slots} active, {dashboard.filling_slots} filling)
      </span>
    </div>
  );
}
