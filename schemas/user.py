"""
User Pydantic schemas — request and response validation.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


# --- Response Schemas ---

class UserResponse(BaseModel):
    """Public user profile — returned from /me and /register."""
    id: int
    email: str
    full_name: Optional[str] = None
    is_active: bool
    is_verified: bool
    is_admin: bool
    daily_quota: int
    quota_used: int
    price_plan: Optional[str] = None
    price: Optional[int] = None
    expired_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserBrief(BaseModel):
    """Minimal user info for token responses."""
    id: int
    email: str
    full_name: Optional[str] = None

    model_config = {"from_attributes": True}
