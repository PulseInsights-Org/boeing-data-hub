"""
Auth schemas — Cognito authentication models.

Authentication schemas for request/response validation.
Version: 1.0.0
"""

from typing import Optional
from pydantic import BaseModel, Field


class User(BaseModel):
    """User model for current user info from Cognito token."""
    user_id: str = Field(..., description="Unique user identifier (Cognito sub)")
    username: Optional[str] = Field(None, description="Username")
    email: Optional[str] = Field(None, description="User email")
    groups: list[str] = Field(default_factory=list, description="Cognito groups")


class LogoutResponse(BaseModel):
    """Logout response."""
    success: bool = Field(default=True, description="Whether logout completed")
    message: str = Field(default="Logged out successfully", description="Logout result message")
    global_signout_success: bool = Field(default=True, description="Whether Cognito global sign-out succeeded")
