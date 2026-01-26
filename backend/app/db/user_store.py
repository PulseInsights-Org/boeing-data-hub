"""
User database operations for authentication.

Handles user CRUD operations against the users table in Supabase.
"""

import logging
from typing import Optional
from datetime import datetime
from supabase import create_client, Client

from app.core.config import settings

logger = logging.getLogger(__name__)


class UserStore:
    """Database operations for user management."""

    def __init__(self):
        """Initialize Supabase client."""
        self.client: Client = create_client(
            settings.supabase_url,
            settings.supabase_key
        )

    def get_user_by_username(self, username: str) -> Optional[dict]:
        """
        Fetch user by username.

        Returns user dict or None if not found.
        """
        try:
            result = self.client.table("users")\
                .select("*")\
                .eq("username", username)\
                .limit(1)\
                .execute()

            if result.data:
                return result.data[0]
            return None

        except Exception as e:
            logger.error(f"Failed to fetch user {username}: {e}")
            return None

    def get_user_by_id(self, user_id: str) -> Optional[dict]:
        """
        Fetch user by ID.

        Returns user dict or None if not found.
        """
        try:
            result = self.client.table("users")\
                .select("*")\
                .eq("id", user_id)\
                .limit(1)\
                .execute()

            if result.data:
                return result.data[0]
            return None

        except Exception as e:
            logger.error(f"Failed to fetch user by ID {user_id}: {e}")
            return None

    def validate_credentials(self, username: str, password: str) -> Optional[dict]:
        """
        Validate user credentials.

        Returns user dict if credentials are valid, None otherwise.
        Note: In production, use proper password hashing (bcrypt, argon2).
        """
        user = self.get_user_by_username(username)

        if not user:
            logger.warning(f"Login attempt for non-existent user: {username}")
            return None

        # Simple password comparison (for production, use hashed passwords)
        if user.get("password") == password:
            logger.info(f"Successful login for user: {username}")
            return user

        logger.warning(f"Invalid password for user: {username}")
        return None

    def update_last_login(self, user_id: str) -> bool:
        """
        Update the last_login timestamp for a user.

        Returns True if update was successful.
        """
        try:
            self.client.table("users")\
                .update({"last_login": datetime.utcnow().isoformat()})\
                .eq("id", user_id)\
                .execute()

            logger.info(f"Updated last_login for user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update last_login for {user_id}: {e}")
            return False

    def create_user(self, user_id: str, username: str, password: str) -> Optional[dict]:
        """
        Create a new user.

        Returns created user dict or None if failed.
        Note: In production, hash the password before storing.
        """
        try:
            result = self.client.table("users")\
                .insert({
                    "id": user_id,
                    "username": username,
                    "password": password  # In production, hash this!
                })\
                .execute()

            if result.data:
                logger.info(f"Created new user: {username}")
                return result.data[0]
            return None

        except Exception as e:
            logger.error(f"Failed to create user {username}: {e}")
            return None

    def list_users(self) -> list[dict]:
        """
        List all users (without passwords).

        Returns list of user dicts.
        """
        try:
            result = self.client.table("users")\
                .select("id, username, created_at, last_login")\
                .execute()

            return result.data or []

        except Exception as e:
            logger.error(f"Failed to list users: {e}")
            return []


# Singleton instance
_user_store: Optional[UserStore] = None


def get_user_store() -> UserStore:
    """Get or create UserStore singleton."""
    global _user_store
    if _user_store is None:
        _user_store = UserStore()
    return _user_store
