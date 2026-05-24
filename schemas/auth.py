"""
Auth Pydantic schemas — registration, login, and token schemas.
"""

from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from schemas.user import UserBrief


# --- Request Schemas ---

class RegisterRequest(BaseModel):
    """Registration request body."""
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = Field(None, max_length=100)
    price_plan: str = Field("monthly", max_length=50)


class LoginRequest(BaseModel):
    """Login request body."""
    email: EmailStr
    password: str = Field(..., min_length=1)


# --- Response Schemas ---

class TokenResponse(BaseModel):
    """JWT token response — returned after login."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: UserBrief


class MessageResponse(BaseModel):
    """Generic message response."""
    message: str
    success: bool = True
