"""
FastAPI dependencies — reusable injections for routes.
Provides get_db (async session) and get_current_user (JWT auth).
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.security import decode_access_token
from models.user import User

# Bearer token scheme for Swagger UI auto-integration
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Dependency: extract and validate JWT, return current User.
    
    Force Logout check:
    - JWT contains `tv` (token_version at time of issue)
    - If user's current token_version in DB != JWT's tv, token is rejected
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token tidak ditemukan. Silakan login terlebih dahulu.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Decode JWT
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token tidak valid atau sudah expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = int(payload["sub"])
    token_version = payload.get("tv", 0)

    # Fetch user from DB (single efficient query)
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User tidak ditemukan.",
        )

    # Check if account has expired
    from datetime import datetime
    if user.expired_at and datetime.utcnow() > user.expired_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Masa aktif akun Anda telah berakhir. Silakan hubungi Admin untuk melakukan perpanjangan.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akun Anda belum diaktifkan oleh Admin atau telah dinonaktifkan.",
        )

    # Force Logout check: compare token_version
    if user.token_version != token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesi telah berakhir. Silakan login kembali.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Track user online state in Redis asynchronously (5-min TTL)
    try:
        from core.config import get_settings
        import redis.asyncio as async_redis
        settings = get_settings()
        r_client = async_redis.from_url(settings.REDIS_URL)
        await r_client.setex(f"user_active:{user.id}", 300, "online")
        await r_client.close()
    except Exception as e:
        # Ensure API call succeeds even if Redis tracking fails
        pass

    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Dependency: extract current user and verify they are an admin.
    Raises 403 Forbidden if not.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses ditolak. Endpoint ini hanya untuk Administrator.",
        )
    return current_user
