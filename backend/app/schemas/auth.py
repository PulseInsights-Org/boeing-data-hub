"""
Authentication schemas for request/response validation.
"""

from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime


class LoginRequest(BaseModel):
    """Login request with username and password."""
    username: str = Field(..., min_length=1, description="Username")
    password: str = Field(..., min_length=1, description="Password")


class LoginResponse(BaseModel):
    """Login response with session token and user info."""
    token: str = Field(..., description="Session token for authentication")
    user_id: str = Field(..., description="Unique user identifier")
    username: str = Field(..., description="Username")
    expires_in: int = Field(..., description="Token expiry time in seconds")
    message: str = Field(default="Login successful")


class User(BaseModel):
    """User model for current user info."""
    user_id: str = Field(..., description="Unique user identifier")
    username: str = Field(..., description="Username")


class UserProfile(BaseModel):
    """Extended user profile from database."""
    id: str = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    created_at: Optional[datetime] = Field(None, description="Account creation time")
    last_login: Optional[datetime] = Field(None, description="Last login time")


class LogoutResponse(BaseModel):
    """Logout response."""
    success: bool = Field(default=True, description="Whether logout completed")
    message: str = Field(default="Logged out successfully", description="Logout result message")
    global_signout_success: bool = Field(default=True, description="Whether Cognito global sign-out succeeded")
