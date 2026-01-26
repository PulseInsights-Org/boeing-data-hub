import { useState, useCallback, useEffect, useRef } from 'react';
import { RealtimeChannel } from '@supabase/supabase-js';
import {
  BatchStatusResponse,
  BulkOperationResponse,
} from '@/types/product';
import {
  startBulkSearch,
  startBulkPublish,
  listBatches,
  cancelBatch,
  parsePartNumbers,
} from '@/services/bulkService';
import {
  subscribeToBatches,
  unsubscribe,
} from '@/services/realtimeService';

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

  // Realtime subscription reference
  const channelRef = useRef<RealtimeChannel | null>(null);
  const statusFilterRef = useRef<string | null>(null);

  // Keep status filter ref in sync
  useEffect(() => {
    statusFilterRef.current = statusFilter;
  }, [statusFilter]);

  // Handle batch insert from realtime
  const handleBatchInsert = useCallback((batch: BatchStatusResponse) => {
    // Only add if it matches current filter (or no filter)
    const currentFilter = statusFilterRef.current;
    if (currentFilter === 'active' && !['pending', 'processing'].includes(batch.status)) return;
    if (currentFilter && currentFilter !== 'active' && batch.status !== currentFilter) return;

    setActiveBatches(prev => {
      // Check if batch already exists
      const exists = prev.some(b => b.id === batch.id);
      if (exists) return prev;
      // Add to beginning of list
      return [batch, ...prev];
    });
  }, []);

  // Handle batch update from realtime
  const handleBatchUpdate = useCallback((batch: BatchStatusResponse) => {
    setActiveBatches(prev => {
      const index = prev.findIndex(b => b.id === batch.id);
      if (index >= 0) {
        // Update existing batch
        const updated = [...prev];
        updated[index] = batch;
        return updated;
      }
      // If not found and matches filter, add it
      const currentFilter = statusFilterRef.current;
      if (currentFilter === 'active' && !['pending', 'processing'].includes(batch.status)) return prev;
      if (currentFilter && currentFilter !== 'active' && batch.status !== currentFilter) return prev;
      return [batch, ...prev];
    });
  }, []);

  // Handle batch delete from realtime
  const handleBatchDelete = useCallback((oldBatch: { id: string }) => {
    setActiveBatches(prev => prev.filter(b => b.id !== oldBatch.id));
  }, []);

  // Set up Supabase Realtime subscription
  useEffect(() => {
    console.log('[useBulkOperations] Setting up Supabase Realtime subscription');

    // Subscribe to batches table changes
    channelRef.current = subscribeToBatches(
      handleBatchInsert,
      handleBatchUpdate,
      handleBatchDelete
    );

    // Cleanup on unmount
    return () => {
      if (channelRef.current) {
        console.log('[useBulkOperations] Cleaning up Realtime subscription');
        unsubscribe(channelRef.current);
        channelRef.current = null;
      }
    };
  }, [handleBatchInsert, handleBatchUpdate, handleBatchDelete]);

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

      // Realtime will automatically update the batches list
      return response;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to start bulk search';
      setError(errorMessage);
      return null;
    } finally {
      setIsStarting(false);
    }
  }, []);

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

      // Realtime will automatically update the batches list
      return response;
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to start bulk publish';
      setError(errorMessage);
      return null;
    } finally {
      setIsStarting(false);
    }
  }, []);

  // Cancel a batch operation
  const cancelBatchOperation = useCallback(async (batchId: string): Promise<boolean> => {
    try {
      await cancelBatch(batchId);
      // Realtime will update the batch status automatically
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
    } catch (err) {
      console.error('Failed to refresh batches:', err);
    }
  }, [statusFilter]);

  // Load initial batches on mount
  useEffect(() => {
    refreshBatches();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  // Update status filter and refresh
  const handleSetStatusFilter = useCallback((status: string | null) => {
    setStatusFilter(status);
    // Refresh with new filter
    const filterStatus = status === 'active' ? undefined : status;
    listBatches(20, 0, filterStatus || undefined).then(response => {
      // For 'active' filter, filter client-side
      if (status === 'active') {
        setActiveBatches(response.batches.filter(b =>
          ['pending', 'processing'].includes(b.status)
        ));
      } else {
        setActiveBatches(response.batches);
      }
    }).catch(err => {
      console.error('Failed to refresh batches:', err);
    });
  }, []);

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
