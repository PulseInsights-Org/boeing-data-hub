/**
 * Protected Route Component.
 *
 * Wraps routes that require authentication.
 * AuthContext handles redirect to Aviation Gateway if not authenticated.
 */

import { ReactNode } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { Loader2 } from 'lucide-react';

interface ProtectedRouteProps {
  children: ReactNode;
}

const ProtectedRoute = ({ children }: ProtectedRouteProps) => {
  const { isAuthenticated, isLoading } = useAuth();

  // Show loading spinner while checking auth state
  // AuthContext will redirect to Aviation Gateway if not authenticated
  if (isLoading || !isAuthenticated) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <p className="text-muted-foreground">Loading...</p>
        </div>
      </div>
    );
  }

  // Render the protected content
  return <>{children}</>;
};

export default ProtectedRoute;
