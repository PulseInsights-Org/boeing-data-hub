import { ProductStatus } from '@/types/product';
import { cn } from '@/lib/utils';

interface StatusBadgeProps {
  status: ProductStatus;
  className?: string;
}

const statusConfig: Record<ProductStatus, { label: string; className: string }> = {
  fetched: {
    label: 'Fetched',
    className: 'bg-muted text-muted-foreground border-border',
  },
  enriched: {
    label: 'Enriched',
    className: 'bg-warning/10 text-warning border-warning/30',
  },
  normalized: {
    label: 'Normalized',
    className: 'bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-900/30 dark:text-amber-400 dark:border-amber-700',
  },
  published: {
    label: 'Published',
    className: 'bg-success/10 text-success border-success/30',
  },
};

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const config = statusConfig[status];

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium border',
        config.className,
        className
      )}
    >
      <span
        className={cn(
          'mr-1.5 h-1.5 w-1.5 rounded-full',
          status === 'fetched' && 'bg-muted-foreground',
          status === 'enriched' && 'bg-warning',
          status === 'normalized' && 'bg-amber-500',
          status === 'published' && 'bg-success'
        )}
      />
      {config.label}
    </span>
  );
}
