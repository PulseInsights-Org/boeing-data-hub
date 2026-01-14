import { Plane, Database } from 'lucide-react';

export function Header() {
  return (
    <header className="border-b border-border bg-card">
      <div className="flex h-16 items-center justify-between px-6">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary">
            <Plane className="h-5 w-5 text-primary-foreground" />
          </div>
          <div>
            <h1 className="text-lg font-semibold text-foreground">
              Boeing Product Normalization & Publishing Dashboard
            </h1>
            <p className="text-sm text-muted-foreground">
              Ingest, normalize, and publish to Shopify
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Database className="h-4 w-4" />
          <span>Boeing Commerce Connect</span>
        </div>
      </div>
    </header>
  );
}
