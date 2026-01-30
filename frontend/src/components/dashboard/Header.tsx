import { Plane, LogOut, Home } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';

const AVIATION_GATEWAY_URL = import.meta.env.VITE_AVIATION_GATEWAY_URL || 'http://localhost:8080';

export function Header() {
  const { logout } = useAuth();

  const handleBackToGateway = () => {
    window.location.href = AVIATION_GATEWAY_URL;
  };

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
        <div className="flex items-center gap-4">
          {/* Back to Aviation Gateway button */}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={handleBackToGateway}
                className="h-9 w-9"
              >
                <Home className="h-5 w-5" />
                <span className="sr-only">Back to Aviation Gateway</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>Back to Aviation Gateway</p>
            </TooltipContent>
          </Tooltip>

          {/* Logout button */}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={logout}
                className="h-9 w-9 text-muted-foreground hover:text-destructive"
              >
                <LogOut className="h-5 w-5" />
                <span className="sr-only">Log out</span>
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              <p>Log out</p>
            </TooltipContent>
          </Tooltip>
        </div>
      </div>
    </header>
  );
}
