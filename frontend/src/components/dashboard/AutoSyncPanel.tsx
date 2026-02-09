/**
 * AutoSyncPanel Component
 *
 * Main container for the Auto-Sync dashboard.
 * Displays sync status, hourly distribution, history, and failures.
 */

import { useState } from 'react';
import {
  RefreshCw,
  Settings2,
  Search,
  Pause,
  Play,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { ErrorAlert } from '@/components/dashboard/ErrorAlert';
import { SyncStatusCards, SyncModeIndicator, EfficiencyBadge } from '@/components/dashboard/SyncStatusCards';
import { HourlyDistributionChart } from '@/components/dashboard/HourlyDistributionChart';
import { SyncHistoryTable } from '@/components/dashboard/SyncHistoryTable';
import { FailedProductsList } from '@/components/dashboard/FailedProductsList';
import { useSyncDashboard } from '@/hooks/useSyncDashboard';
import { cn } from '@/lib/utils';

export function AutoSyncPanel() {
  const {
    dashboard,
    history,
    failures,
    hourlyStats,
    currentHour,
    isLoading,
    isRefreshing,
    error,
    refresh,
    reactivate,
    triggerSync,
    clearError,
    autoRefreshEnabled,
    setAutoRefreshEnabled,
  } = useSyncDashboard();

  const [activeTab, setActiveTab] = useState<'overview' | 'history' | 'failures'>('overview');
  const [searchQuery, setSearchQuery] = useState('');

  return (
    <div className="flex-1 overflow-auto">
      <div className="p-6 space-y-6">
        {/* Header */}
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-xl font-semibold">Auto-Sync Dashboard</h2>
            <p className="text-sm text-muted-foreground">
              Monitor and manage automatic product synchronization with Boeing
            </p>
          </div>
          <div className="flex items-center gap-3">
            <SyncModeIndicator dashboard={dashboard} />
            <EfficiencyBadge dashboard={dashboard} />

            <div className="flex items-center gap-1">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-9 w-9"
                    onClick={() => setAutoRefreshEnabled(!autoRefreshEnabled)}
                  >
                    {autoRefreshEnabled ? (
                      <Pause className="h-4 w-4" />
                    ) : (
                      <Play className="h-4 w-4" />
                    )}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {autoRefreshEnabled ? 'Pause auto-refresh' : 'Enable auto-refresh'}
                </TooltipContent>
              </Tooltip>

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="icon"
                    className="h-9 w-9"
                    onClick={refresh}
                    disabled={isRefreshing}
                  >
                    <RefreshCw className={cn('h-4 w-4', isRefreshing && 'animate-spin')} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Refresh dashboard</TooltipContent>
              </Tooltip>
            </div>
          </div>
        </div>

        {/* Error Alert */}
        {error && (
          <ErrorAlert message={error} onDismiss={clearError} />
        )}

        {/* Status Cards */}
        <SyncStatusCards dashboard={dashboard} isLoading={isLoading} />

        {/* Main Content Tabs */}
        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as any)}>
          <TabsList className="grid w-full grid-cols-3 lg:w-auto lg:inline-grid">
            <TabsTrigger value="overview" className="gap-2">
              <Settings2 className="h-4 w-4" />
              Overview
            </TabsTrigger>
            <TabsTrigger value="history" className="gap-2">
              <RefreshCw className="h-4 w-4" />
              Sync History
              {history.length > 0 && (
                <Badge variant="secondary" className="ml-1 text-xs">
                  {history.length}
                </Badge>
              )}
            </TabsTrigger>
            <TabsTrigger value="failures" className="gap-2">
              <Search className="h-4 w-4" />
              Failures
              {failures.length > 0 && (
                <Badge variant="destructive" className="ml-1 text-xs">
                  {failures.length}
                </Badge>
              )}
            </TabsTrigger>
          </TabsList>

          {/* Overview Tab */}
          <TabsContent value="overview" className="mt-6 space-y-6">
            {/* Hourly Distribution Chart */}
            <HourlyDistributionChart
              hourlyStats={hourlyStats}
              currentHour={currentHour}
              dashboard={dashboard}
              isLoading={isLoading}
            />

            {/* Two-column layout for history and failures preview */}
            <div className="grid gap-6 lg:grid-cols-2">
              {/* Recent Activity Preview */}
              <Card>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-base">Recent Activity</CardTitle>
                    <Button
                      variant="link"
                      size="sm"
                      className="h-auto p-0"
                      onClick={() => setActiveTab('history')}
                    >
                      View all
                    </Button>
                  </div>
                </CardHeader>
                <CardContent>
                  {history.length > 0 ? (
                    <div className="space-y-2">
                      {history.slice(0, 5).map((item, index) => (
                        <div
                          key={`${item.sku}-${index}`}
                          className="flex items-center justify-between py-2 border-b last:border-0"
                        >
                          <div className="flex items-center gap-2">
                            <Badge
                              variant="secondary"
                              className={cn(
                                'text-xs',
                                item.sync_status === 'success' && 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400',
                                item.sync_status === 'failed' && 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                              )}
                            >
                              {item.sync_status}
                            </Badge>
                            <span className="font-mono text-sm truncate max-w-[200px]">
                              {item.sku}
                            </span>
                          </div>
                          <span className="text-xs text-muted-foreground">
                            ${item.last_price?.toFixed(2) ?? '—'} / {item.last_quantity ?? '—'}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-center py-6 text-muted-foreground">
                      <p>No recent sync activity</p>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Failures Preview */}
              <Card>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-base">Sync Issues</CardTitle>
                    {failures.length > 0 && (
                      <Button
                        variant="link"
                        size="sm"
                        className="h-auto p-0"
                        onClick={() => setActiveTab('failures')}
                      >
                        View all
                      </Button>
                    )}
                  </div>
                </CardHeader>
                <CardContent>
                  {failures.length > 0 ? (
                    <div className="space-y-2">
                      {failures.slice(0, 5).map((product) => (
                        <div
                          key={product.sku}
                          className="flex items-center justify-between py-2 border-b last:border-0"
                        >
                          <div className="flex items-center gap-2">
                            <Badge
                              variant="secondary"
                              className={cn(
                                'text-xs',
                                !product.is_active
                                  ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                                  : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
                              )}
                            >
                              {product.consecutive_failures}x
                            </Badge>
                            <span className="font-mono text-sm truncate max-w-[200px]">
                              {product.sku}
                            </span>
                          </div>
                          {!product.is_active && (
                            <Badge variant="destructive" className="text-xs">
                              Deactivated
                            </Badge>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-center py-6 text-emerald-600 dark:text-emerald-400">
                      <RefreshCw className="h-8 w-8 mx-auto mb-2 opacity-50" />
                      <p className="font-medium">All products healthy</p>
                      <p className="text-sm text-muted-foreground">No sync failures</p>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* Sync Configuration Info */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Sync Configuration</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  <div className="space-y-1">
                    <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                      Sync Mode
                    </p>
                    <p className="text-sm font-medium">
                      {dashboard?.sync_mode === 'testing' ? 'Testing (10-min buckets)' : 'Production (Hourly)'}
                    </p>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                      Total Buckets
                    </p>
                    <p className="text-sm font-medium">
                      {dashboard?.max_buckets || 24}
                    </p>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                      Boeing Rate Limit
                    </p>
                    <p className="text-sm font-medium">2 requests/min</p>
                  </div>
                  <div className="space-y-1">
                    <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                      Batch Size
                    </p>
                    <p className="text-sm font-medium">10 SKUs per call</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* History Tab */}
          <TabsContent value="history" className="mt-6">
            <SyncHistoryTable history={history} isLoading={isLoading} />
          </TabsContent>

          {/* Failures Tab */}
          <TabsContent value="failures" className="mt-6">
            <FailedProductsList
              failures={failures}
              isLoading={isLoading}
              onReactivate={reactivate}
              onTriggerSync={triggerSync}
            />
          </TabsContent>
        </Tabs>

        {/* Footer with auto-refresh indicator */}
        <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground pt-4 border-t">
          <span className={cn(
            'w-2 h-2 rounded-full',
            autoRefreshEnabled ? 'bg-emerald-500 animate-pulse' : 'bg-muted-foreground'
          )} />
          {autoRefreshEnabled ? (
            <span>Auto-refreshing every 30 seconds</span>
          ) : (
            <span>Auto-refresh paused</span>
          )}
          {dashboard?.last_updated && (
            <span className="text-muted-foreground/60">
              • Last updated: {new Date(dashboard.last_updated).toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
