/**
 * Authentication Context for Boeing Data Hub.
 *
 * Provides authentication state and actions to the entire application.
 * Handles:
 * - Login/logout
 * - Session persistence
 * - Auth state management
 */

import React, { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import { User, LoginCredentials, AuthState } from '@/types/auth';
import {
  login as authLogin,
  logout as authLogout,
  getStoredUser,
  getStoredToken,
  isAuthenticated as checkIsAuthenticated,
} from '@/services/authService';

/**
 * Auth context value interface.
 */
interface AuthContextValue extends AuthState {
  login: (credentials: LoginCredentials) => Promise<void>;
  logout: () => Promise<void>;
}

// Create context with undefined default
const AuthContext = createContext<AuthContextValue | undefined>(undefined);

/**
 * Auth provider props.
 */
interface AuthProviderProps {
  children: ReactNode;
}

/**
 * Authentication Provider Component.
 * Wraps the application to provide auth state and actions.
 */
export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [state, setState] = useState<AuthState>({
    user: null,
    token: null,
    isAuthenticated: false,
    isLoading: true,
  });

  // Initialize auth state from localStorage on mount
  useEffect(() => {
    const initializeAuth = () => {
      const token = getStoredToken();
      const user = getStoredUser();

      if (token && user) {
        setState({
          user,
          token,
          isAuthenticated: true,
          isLoading: false,
        });
      } else {
        setState({
          user: null,
          token: null,
          isAuthenticated: false,
          isLoading: false,
        });
      }
    };

    initializeAuth();
  }, []);

  /**
   * Login with credentials.
   */
  const login = useCallback(async (credentials: LoginCredentials): Promise<void> => {
    setState(prev => ({ ...prev, isLoading: true }));

    try {
      const response = await authLogin(credentials);

      setState({
        user: { user_id: response.user_id, username: response.username },
        token: response.token,
        isAuthenticated: true,
        isLoading: false,
      });
    } catch (error) {
      setState(prev => ({ ...prev, isLoading: false }));
      throw error;
    }
  }, []);

  /**
   * Logout and clear auth state.
   */
  const logout = useCallback(async (): Promise<void> => {
    setState(prev => ({ ...prev, isLoading: true }));

    try {
      await authLogout();
    } finally {
      setState({
        user: null,
        token: null,
        isAuthenticated: false,
        isLoading: false,
      });
    }
  }, []);

  const value: AuthContextValue = {
    ...state,
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

/**
 * Hook to access authentication context.
 * Must be used within an AuthProvider.
 */
export const useAuth = (): AuthContextValue => {
  const context = useContext(AuthContext);

  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }

  return context;
};

export default AuthContext;
