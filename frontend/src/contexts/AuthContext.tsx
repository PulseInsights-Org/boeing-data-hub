/**
 * Auth Context for Boeing Data Hub
 *
 * Handles SSO token received from Aviation Gateway via URL fragment.
 * Token is extracted on app load and stored in sessionStorage.
 *
 * Supports federated Single Sign-Out (SLO) by:
 * 1. Embedding a hidden iframe to Aviation Gateway's /logout-listener
 * 2. Listening for logout events via postMessage
 * 3. Automatically logging out when Aviation Gateway broadcasts logout
 */
import { createContext, useContext, useState, useEffect, useCallback, useRef, ReactNode } from 'react';

interface User {
  id: string;
  email: string;
  groups: string[];
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  logout: () => void;
  getAuthHeader: () => Record<string, string>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const AVIATION_GATEWAY_URL = import.meta.env.VITE_AVIATION_GATEWAY_URL || 'http://localhost:8080';
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';
const API_ROOT_URL = API_BASE_URL.replace(/\/api\/v1$/, '');
const TOKEN_STORAGE_KEY = 'boeing_data_hub_sso_token';
const LOGOUT_LISTENER_URL = `${AVIATION_GATEWAY_URL}/logout-listener`;
const LOGOUT_EVENT_TYPE = 'AVIATION_GATEWAY_LOGOUT';

/**
 * Parse JWT token to extract payload (without verification)
 * Verification is done server-side
 */
function parseJwt(token: string): Record<string, unknown> | null {
  try {
    const base64Url = token.split('.')[1];
    const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
    const jsonPayload = decodeURIComponent(
      atob(base64)
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    );
    return JSON.parse(jsonPayload);
  } catch {
    return null;
  }
}

/**
 * Check if token is expired
 */
function isTokenExpired(token: string): boolean {
  const payload = parseJwt(token);
  if (!payload || typeof payload.exp !== 'number') {
    return true;
  }
  // Add 30 second buffer for network latency
  return Date.now() >= (payload.exp * 1000) - 30000;
}

/**
 * Extract user info from Cognito JWT token
 */
function extractUserFromToken(token: string): User | null {
  const payload = parseJwt(token);
  if (!payload) return null;

  return {
    id: payload.sub as string,
    email: payload.email as string || '',
    groups: (payload['cognito:groups'] as string[]) || [],
  };
}

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const logoutIframeRef = useRef<HTMLIFrameElement | null>(null);

  /**
   * Extract token from URL fragment (SSO flow from Aviation Gateway)
   */
  const extractTokenFromFragment = useCallback(() => {
    const hash = window.location.hash;
    if (!hash) return null;

    const params = new URLSearchParams(hash.substring(1));
    const accessToken = params.get('access_token');

    if (accessToken) {
      // Clear the fragment from URL immediately for security
      window.history.replaceState({}, document.title, window.location.pathname + window.location.search);
      return accessToken;
    }
    return null;
  }, []);

  /**
   * Redirect to Aviation Gateway for authentication
   */
  const redirectToLogin = useCallback(() => {
    const currentUrl = window.location.href.split('#')[0];
    const loginUrl = `${AVIATION_GATEWAY_URL}/login?redirect=${encodeURIComponent(currentUrl)}`;
    window.location.href = loginUrl;
  }, []);

  /**
   * Initialize auth state on mount
   */
  useEffect(() => {
    const initAuth = () => {
      // First, check for token in URL fragment (SSO redirect)
      const fragmentToken = extractTokenFromFragment();

      if (fragmentToken) {
        if (!isTokenExpired(fragmentToken)) {
          sessionStorage.setItem(TOKEN_STORAGE_KEY, fragmentToken);
          setToken(fragmentToken);
          setUser(extractUserFromToken(fragmentToken));
        } else {
          console.warn('SSO token expired, redirecting to login');
          redirectToLogin();
          return;
        }
      } else {
        // Check for existing token in sessionStorage
        const storedToken = sessionStorage.getItem(TOKEN_STORAGE_KEY);
        if (storedToken && !isTokenExpired(storedToken)) {
          setToken(storedToken);
          setUser(extractUserFromToken(storedToken));
        } else if (storedToken) {
          // Token expired, clear and redirect
          sessionStorage.removeItem(TOKEN_STORAGE_KEY);
          redirectToLogin();
          return;
        } else {
          // No token at all, redirect to Aviation Gateway
          redirectToLogin();
          return;
        }
      }

      setIsLoading(false);
    };

    initAuth();
  }, [extractTokenFromFragment, redirectToLogin]);

  /**
   * Handle federated logout from Aviation Gateway
   * This is called when we receive a logout event via postMessage
   */
  const handleFederatedLogout = useCallback(() => {
    sessionStorage.removeItem(TOKEN_STORAGE_KEY);
    setToken(null);
    setUser(null);
    // Redirect to Aviation Gateway login page
    redirectToLogin();
  }, [redirectToLogin]);

  /**
   * Set up Single Sign-Out (SLO) listener
   * Embeds a hidden iframe to Aviation Gateway's logout-listener page
   * and listens for logout events via postMessage
   */
  useEffect(() => {
    // Only set up SLO if user is authenticated
    if (!token) return;

    // Create hidden iframe for logout listener
    const iframe = document.createElement('iframe');
    iframe.src = LOGOUT_LISTENER_URL;
    iframe.style.display = 'none';
    iframe.style.width = '0';
    iframe.style.height = '0';
    iframe.style.border = 'none';
    iframe.setAttribute('aria-hidden', 'true');
    iframe.setAttribute('tabindex', '-1');
    document.body.appendChild(iframe);
    logoutIframeRef.current = iframe;

    // Listen for logout events from the iframe
    const handleMessage = (event: MessageEvent) => {
      // SECURITY: Validate origin - must be from Aviation Gateway
      if (event.origin !== AVIATION_GATEWAY_URL) {
        return;
      }

      // Check if this is a logout event
      if (event.data?.type === LOGOUT_EVENT_TYPE && event.data?.source === 'aviation-gateway') {
        console.log('Received federated logout event from Aviation Gateway');
        handleFederatedLogout();
      }
    };

    window.addEventListener('message', handleMessage);

    // Cleanup
    return () => {
      window.removeEventListener('message', handleMessage);
      if (logoutIframeRef.current && document.body.contains(logoutIframeRef.current)) {
        document.body.removeChild(logoutIframeRef.current);
      }
      logoutIframeRef.current = null;
    };
  }, [token, handleFederatedLogout]);

  /**
   * Logout - performs Cognito global sign-out and redirects to Aviation Gateway
   *
   * Simplified approach:
   * 1. Call backend to perform Cognito GlobalSignOut (revokes all refresh tokens)
   * 2. Clear local session storage
   * 3. Redirect to Aviation Gateway
   * 4. Aviation Gateway validates session with Cognito
   * 5. Cognito rejects revoked token → Aviation Gateway logs out automatically
   */
  const logout = useCallback(async () => {
    try {
      // Call backend logout endpoint to perform Cognito global sign-out
      if (token) {
        try {
          console.log('Calling backend GlobalSignOut API...');
          const response = await fetch(`${API_ROOT_URL}/api/v1/auth/logout`, {
            method: 'POST',
            headers: {
              'Authorization': `Bearer ${token}`,
              'Content-Type': 'application/json',
            },
          });

          if (response.ok) {
            const result = await response.json();
            console.log('GlobalSignOut successful:', result);
          } else {
            console.warn('Backend logout failed, continuing with local logout');
          }
        } catch (error) {
          console.error('Error calling logout endpoint:', error);
          // Continue with local logout even if backend call fails
        }
      }

      // Clear local state
      console.log('Clearing local session storage...');
      sessionStorage.removeItem(TOKEN_STORAGE_KEY);
      setToken(null);
      setUser(null);

      // Redirect to Aviation Gateway logout endpoint for explicit logout
      console.log('Redirecting to Aviation Gateway logout...');
      window.location.href = `${AVIATION_GATEWAY_URL}/logout`;
    } catch (error) {
      console.error('Logout error:', error);
      // Ensure we clean up and redirect even on error
      sessionStorage.removeItem(TOKEN_STORAGE_KEY);
      setToken(null);
      setUser(null);
      window.location.href = `${AVIATION_GATEWAY_URL}/logout`;
    }
  }, [token]);

  /**
   * Get Authorization header for API requests
   */
  const getAuthHeader = useCallback((): Record<string, string> => {
    if (!token) return {};
    return { Authorization: `Bearer ${token}` };
  }, [token]);

  const value: AuthContextType = {
    user,
    token,
    isAuthenticated: !!token && !!user,
    isLoading,
    logout,
    getAuthHeader,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}

export default AuthContext;
