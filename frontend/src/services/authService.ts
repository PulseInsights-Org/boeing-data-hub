/**
 * Authentication Service for Boeing Data Hub.
 *
 * Handles:
 * - Login/logout API calls
 * - Token storage in localStorage
 * - Session validation
 */

import { LoginCredentials, LoginResponse, User, LogoutResponse } from '@/types/auth';

// Base URL for backend API
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

// localStorage keys
const TOKEN_KEY = 'boeing_hub_token';
const USER_KEY = 'boeing_hub_user';
const EXPIRY_KEY = 'boeing_hub_expiry';

/**
 * Login with username and password.
 * Stores token and user info in localStorage on success.
 */
export const login = async (credentials: LoginCredentials): Promise<LoginResponse> => {
  const url = new URL('/api/auth/login', API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    },
    body: JSON.stringify(credentials),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Invalid username or password');
  }

  const data: LoginResponse = await response.json();

  // Store auth data in localStorage
  const expiryTime = Date.now() + data.expires_in * 1000;
  localStorage.setItem(TOKEN_KEY, data.token);
  localStorage.setItem(USER_KEY, JSON.stringify({ user_id: data.user_id, username: data.username }));
  localStorage.setItem(EXPIRY_KEY, expiryTime.toString());

  return data;
};

/**
 * Logout and clear stored credentials.
 */
export const logout = async (): Promise<void> => {
  const token = getStoredToken();

  if (token) {
    try {
      const url = new URL('/api/auth/logout', API_BASE_URL || window.location.origin);
      await fetch(url.toString(), {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/json',
        },
      });
    } catch (error) {
      // Ignore errors during logout - still clear local storage
      console.warn('Logout API call failed:', error);
    }
  }

  // Clear local storage
  clearStoredAuth();
};

/**
 * Get the stored authentication token.
 * Returns null if token is missing or expired.
 */
export const getStoredToken = (): string | null => {
  const token = localStorage.getItem(TOKEN_KEY);
  const expiryStr = localStorage.getItem(EXPIRY_KEY);

  if (!token || !expiryStr) {
    return null;
  }

  const expiry = parseInt(expiryStr, 10);
  if (Date.now() > expiry) {
    // Token expired - clear storage
    clearStoredAuth();
    return null;
  }

  return token;
};

/**
 * Get the stored user information.
 * Returns null if not logged in or session expired.
 */
export const getStoredUser = (): User | null => {
  const token = getStoredToken();
  if (!token) {
    return null;
  }

  const userStr = localStorage.getItem(USER_KEY);
  if (!userStr) {
    return null;
  }

  try {
    return JSON.parse(userStr) as User;
  } catch {
    return null;
  }
};

/**
 * Check if the user is currently authenticated.
 */
export const isAuthenticated = (): boolean => {
  return getStoredToken() !== null;
};

/**
 * Get current user info from the API.
 * Validates the stored token with the backend.
 */
export const getCurrentUser = async (): Promise<User> => {
  const token = getStoredToken();

  if (!token) {
    throw new Error('Not authenticated');
  }

  const url = new URL('/api/auth/me', API_BASE_URL || window.location.origin);

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json',
    },
  });

  if (!response.ok) {
    // Token invalid - clear storage
    clearStoredAuth();
    throw new Error('Session expired');
  }

  return response.json();
};

/**
 * Clear all stored authentication data.
 */
export const clearStoredAuth = (): void => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(EXPIRY_KEY);
};

/**
 * Get authorization headers for API requests.
 * Returns empty object if not authenticated.
 */
export const getAuthHeaders = (): Record<string, string> => {
  const token = getStoredToken();
  if (!token) {
    return {};
  }
  return {
    Authorization: `Bearer ${token}`,
  };
};
