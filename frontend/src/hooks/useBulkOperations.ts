import { useState, useCallback, useEffect, useRef } from 'react';
import {
  BatchStatusResponse,
  BulkOperationResponse,
} from '@/types/product';
import {
  startBulkSearch,
  startBulkPublish,
  getBatchStatus,
  listBatches,
  cancelBatch,
  parsePartNumbers,
} from '@/services/bulkService';

// Polling interval in milliseconds
const POLLING_INTERVAL = 2000;

interface UseBulkOperationsReturn {
  // State
  activeBatches: BatchStatusResponse[];
  isStarting: boolean;
  error: string | null;
  statusFilter: string | null;

  // Actions
  startBulkSearchOperation: (partNumbersText: string, idempotencyKey?: string) => Promise<BulkOperationResponse | null>;
  startBulkPublishOperation: (partNumbersText: string, idempotencyKey?: string) => Promise<BulkOperationResponse | null>;
  cancelBatchOperation: (batchId: string) => Promise<boolean>;
  refreshBatches: (status?: string) => Promise<void>;
  setStatusFilter: (status: string | null) => void;
  clearError: () => void;
}

export function useBulkOperations(): UseBulkOperationsReturn {
  const [activeBatches, setActiveBatches] = useState<BatchStatusResponse[]>([]);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string | null>(null);

  // Track batches being polled
  const pollingRef = useRef<Map<string, NodeJS.Timeout>>(new Map());

  // Poll a specific batch for status updates
  const pollBatch = useCallback(async (batchId: string) => {
    try {
      const status = await getBatchStatus(batchId);

      setActiveBatches(prev => {
        const index = prev.findIndex(b => b.id === batchId);
        if (index >= 0) {
          const updated = [...prev];
          updated[index] = status;
          return updated;
        }
        return [...prev, status];
      });

      // Stop polling if batch is completed, failed, or cancelled
      if (['completed', 'failed', 'cancelled'].includes(status.status)) {
        const intervalId = pollingRef.current.get(batchId);
        if (intervalId) {
          clearInterval(intervalId);
          pollingRef.current.delete(batchId);
        }
      }
    } catch (err) {
      console.error(`Failed to poll batch ${batchId}:`, err);
    }
  }, []);

  // Start polling for a batch
  const startPolling = useCallback((batchId: string) => {
    // Don't start if already polling
    if (pollingRef.current.has(batchId)) return;

    // Initial poll
    pollBatch(batchId);

    // Set up interval polling
    const intervalId = setInterval(() => pollBatch(batchId), POLLING_INTERVAL);
    pollingRef.current.set(batchId, intervalId);
  }, [pollBatch]);

  // Stop all polling on unmount
  useEffect(() => {
    return () => {
      pollingRef.current.forEach(intervalId => clearInterval(intervalId));
      pollingRef.current.clear();
    };
  }, []);

  // Start a bulk search operation
  const startBulkSearchOperation = useCallback(async (
    partNumbersText: string,
    idempotencyKey?: string
  ): Promise<BulkOperationResponse | null> => {
    const partNumbers = parsePartNumbers(partNumbersText);
    if (partNumbers.length === 0) {
      setError('No valid part numbers provided');
      return null;
    }

    setIsStarting(true);
    setError(null);

    try {
      const response = await startBulkSearch({
        part_numbers: partNumbers,
        idempotency_key: idempotencyKey,
      });

      // Start polling for this batch
      startPolling(response.batch_id);

      return response;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to start bulk search';
      setError(errorMessage);
      return null;
    } finally {
      setIsStarting(false);
    }
  }, [startPolling]);

  // Start a bulk publish operation
  const startBulkPublishOperation = useCallback(async (
    partNumbersText: string,
    idempotencyKey?: string
  ): Promise<BulkOperationResponse | null> => {
    const partNumbers = parsePartNumbers(partNumbersText);
    if (partNumbers.length === 0) {
      setError('No valid part numbers provided');
      return null;
    }

    setIsStarting(true);
    setError(null);

    try {
      const response = await startBulkPublish({
        part_numbers: partNumbers,
        idempotency_key: idempotencyKey,
      });

      // Start polling for this batch
      startPolling(response.batch_id);

      return response;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to start bulk publish';
      setError(errorMessage);
      return null;
    } finally {
      setIsStarting(false);
    }
  }, [startPolling]);

  // Cancel a batch operation
  const cancelBatchOperation = useCallback(async (batchId: string): Promise<boolean> => {
    try {
      await cancelBatch(batchId);

      // Update local state
      setActiveBatches(prev =>
        prev.map(b =>
          b.id === batchId ? { ...b, status: 'cancelled' as const } : b
        )
      );

      // Stop polling
      const intervalId = pollingRef.current.get(batchId);
      if (intervalId) {
        clearInterval(intervalId);
        pollingRef.current.delete(batchId);
      }

      return true;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to cancel batch';
      setError(errorMessage);
      return false;
    }
  }, []);

  // Refresh all batches from server with optional status filter
  const refreshBatches = useCallback(async (status?: string) => {
    try {
      const filterStatus = status !== undefined ? status : statusFilter;
      const response = await listBatches(20, 0, filterStatus || undefined);
      setActiveBatches(response.batches);

      // Start polling for any active batches
      response.batches.forEach(batch => {
        if (['pending', 'processing'].includes(batch.status)) {
          startPolling(batch.id);
        }
      });
    } catch (err) {
      console.error('Failed to refresh batches:', err);
    }
  }, [startPolling, statusFilter]);

  // Load initial batches on mount
  useEffect(() => {
    refreshBatches();
  }, [refreshBatches]);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  // Update status filter and refresh
  const handleSetStatusFilter = useCallback((status: string | null) => {
    setStatusFilter(status);
    refreshBatches(status || undefined);
  }, [refreshBatches]);

  return {
    activeBatches,
    isStarting,
    error,
    statusFilter,
    startBulkSearchOperation,
    startBulkPublishOperation,
    cancelBatchOperation,
    refreshBatches,
    setStatusFilter: handleSetStatusFilter,
    clearError,
  };
}
