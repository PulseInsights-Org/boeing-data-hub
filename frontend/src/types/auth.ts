/**
 * Authentication types for the Boeing Data Hub.
 */

/**
 * User information returned from the API.
 */
export interface User {
  user_id: string;
  username: string;
}

/**
 * Login credentials for authentication.
 */
export interface LoginCredentials {
  username: string;
  password: string;
}

/**
 * Response from the login API endpoint.
 */
export interface LoginResponse {
  token: string;
  user_id: string;
  username: string;
  expires_in: number;
  message: string;
}

/**
 * Authentication state in the application.
 */
export interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
}

/**
 * Logout response from the API.
 */
export interface LogoutResponse {
  message: string;
  success: boolean;
}
