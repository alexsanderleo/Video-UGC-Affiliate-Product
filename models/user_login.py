"""
UserLogin model — tracks user login history, IP addresses, and device signatures.
"""

from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship

from models.base import Base, TimestampMixin


class UserLogin(TimestampMixin, Base):
    """
    UserLogins table.
    
    Used to track login history and devices:
    - ip_address: The IP address of the client at login.
    - user_agent: Raw user agent string.
    - device_brand: Brand/manufacture (e.g. iPhone, Samsung, Xiaomi, Chrome, Safari, etc.).
    - device_os: Operating System (e.g. Android, iOS, Windows, macOS, Linux).
    """

    __tablename__ = "user_logins"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(500), nullable=True)
    device_brand = Column(String(100), nullable=True)
    device_os = Column(String(100), nullable=True)

    # Relationships
    user = relationship("User", backref="logins")

    def __repr__(self) -> str:
        return f"<UserLogin id={self.id} user_id={self.user_id} device={self.device_brand}>"
