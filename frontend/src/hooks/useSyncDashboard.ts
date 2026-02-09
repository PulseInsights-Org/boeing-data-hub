/**
 * useSyncDashboard Hook
 *
 * React hook for managing Auto-Sync dashboard state.
 * Provides data fetching, polling, and actions for the sync dashboard.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  SyncDashboardData,
  SyncHistoryItem,
  FailedProduct,
  HourlyStats,
  fetchSyncDashboard,
  fetchSyncHistory,
  fetchFailedProducts,
  fetchHourlyStats,
  reactivateProduct,
  triggerImmediateSync,
} from '@/services/syncService';

interface UseSyncDashboardReturn {
  // Dashboard data
  dashboard: SyncDashboardData | null;
  history: SyncHistoryItem[];
  failures: FailedProduct[];
  hourlyStats: HourlyStats[];
  currentHour: number;

  // Loading states
  isLoading: boolean;
  isRefreshing: boolean;
  error: string | null;

  // Actions
  refresh: () => Promise<void>;
  reactivate: (sku: string) => Promise<boolean>;
  triggerSync: (sku: string) => Promise<boolean>;
  clearError: () => void;

  // Auto-refresh control
  autoRefreshEnabled: boolean;
  setAutoRefreshEnabled: (enabled: boolean) => void;
}

const POLL_INTERVAL = 30000; // 30 seconds

export function useSyncDashboard(): UseSyncDashboardReturn {
  // State
  const [dashboard, setDashboard] = useState<SyncDashboardData | null>(null);
  const [history, setHistory] = useState<SyncHistoryItem[]>([]);
  const [failures, setFailures] = useState<FailedProduct[]>([]);
  const [hourlyStats, setHourlyStats] = useState<HourlyStats[]>([]);
  const [currentHour, setCurrentHour] = useState<number>(0);

  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [autoRefreshEnabled, setAutoRefreshEnabled] = useState(true);

  // Refs for cleanup
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const isMountedRef = useRef(true);

  // Fetch all dashboard data
  const fetchAllData = useCallback(async (isRefresh: boolean = false) => {
    if (isRefresh) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }

    try {
      // Fetch all data in parallel
      const [dashboardData, historyData, failuresData, hourlyData] = await Promise.all([
        fetchSyncDashboard(),
        fetchSyncHistory(50, 24),
        fetchFailedProducts(50, true),
        fetchHourlyStats(),
      ]);

      if (isMountedRef.current) {
        setDashboard(dashboardData);
        setHistory(historyData.items);
        setFailures(failuresData.products);
        setHourlyStats(hourlyData.hours);
        setCurrentHour(hourlyData.current_hour);
        setError(null);
      }
    } catch (err) {
      if (isMountedRef.current) {
        const message = err instanceof Error ? err.message : 'Failed to fetch sync data';
        setError(message);
        console.error('[useSyncDashboard] Error:', err);
      }
    } finally {
      if (isMountedRef.current) {
        setIsLoading(false);
        setIsRefreshing(false);
      }
    }
  }, []);

  // Initial load
  useEffect(() => {
    isMountedRef.current = true;
    fetchAllData(false);

    return () => {
      isMountedRef.current = false;
    };
  }, [fetchAllData]);

  // Auto-refresh polling
  useEffect(() => {
    if (autoRefreshEnabled) {
      pollIntervalRef.current = setInterval(() => {
        fetchAllData(true);
      }, POLL_INTERVAL);
    }

    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
        pollIntervalRef.current = null;
      }
    };
  }, [autoRefreshEnabled, fetchAllData]);

  // Manual refresh
  const refresh = useCallback(async () => {
    await fetchAllData(true);
  }, [fetchAllData]);

  // Reactivate a product
  const reactivate = useCallback(async (sku: string): Promise<boolean> => {
    try {
      await reactivateProduct(sku);
      // Refresh data after reactivation
      await fetchAllData(true);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to reactivate product';
      setError(message);
      return false;
    }
  }, [fetchAllData]);

  // Trigger immediate sync
  const triggerSync = useCallback(async (sku: string): Promise<boolean> => {
    try {
      await triggerImmediateSync(sku);
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to trigger sync';
      setError(message);
      return false;
    }
  }, []);

  // Clear error
  const clearError = useCallback(() => {
    setError(null);
  }, []);

  return {
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
  };
}
