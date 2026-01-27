/**
 * Authentication Service for Boeing Data Hub.
 *
 * Provides auth headers for API requests using SSO token from sessionStorage.
 * Token is managed by AuthContext via Aviation Gateway SSO.
 */

const TOKEN_STORAGE_KEY = 'boeing_data_hub_sso_token';
const AVIATION_GATEWAY_URL = import.meta.env.VITE_AVIATION_GATEWAY_URL || 'http://localhost:8080';

/**
 * Get authorization headers for API requests.
 * Returns empty object if not authenticated.
 */
export const getAuthHeaders = (): Record<string, string> => {
  const token = sessionStorage.getItem(TOKEN_STORAGE_KEY);
  if (!token) {
    return {};
  }
  return {
    Authorization: `Bearer ${token}`,
  };
};

/**
 * Redirect to Aviation Gateway for authentication.
 * Used when API returns 401.
 */
export const redirectToLogin = (): void => {
  const currentUrl = window.location.href.split('#')[0];
  window.location.href = `${AVIATION_GATEWAY_URL}/login?redirect=${encodeURIComponent(currentUrl)}`;
};

/**
 * Handle 401 response from API.
 * Clears token and redirects to login.
 */
export const handleUnauthorized = (): void => {
  sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  redirectToLogin();
};
