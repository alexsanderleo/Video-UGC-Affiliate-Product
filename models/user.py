"""
User model — core table for SaaS authentication.
Includes token_version for Force Logout and daily_quota for rate limiting.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from models.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    """
    Users table.

    Force Logout Logic:
    - Every JWT contains the user's current `token_version`.
    - When user calls /logout-all, `token_version` is incremented.
    - All existing JWTs with old version are automatically rejected.

    Quota Logic:
    - `daily_quota`: max generates per day (default 5).
    - `quota_used`: counter incremented on each generate.
    - `quota_reset`: timestamp of last reset (auto-resets daily).
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_pw = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=True)

    # Account status
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)

    # Force Logout — increment to invalidate all existing JWTs
    token_version = Column(Integer, default=0, nullable=False)

    # Daily quota
    daily_quota = Column(Integer, default=5, nullable=False)
    quota_used = Column(Integer, default=0, nullable=False)
    quota_reset = Column(DateTime(timezone=True), nullable=True)

    # SaaS pricing plan & expiration
    price_plan = Column(String(50), nullable=True)
    price = Column(Integer, nullable=True)
    expired_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"

