"""
Async SQLAlchemy database engine and session management.
Optimized connection pooling for 10K users on VPS.
"""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import get_settings

settings = get_settings()

# --- Engine Configuration ---
# SQLite doesn't support pool settings, so we conditionally apply them
_engine_kwargs = {
    "echo": settings.DEBUG,
    "future": True,
}

if "sqlite" not in settings.DATABASE_URL:
    # PostgreSQL / MySQL pool optimization
    _engine_kwargs.update({
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_recycle": settings.DB_POOL_RECYCLE,
        "pool_pre_ping": True,  # Check connection health before use
    })

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

# --- Session Factory ---
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Don't expire objects after commit (better performance)
)


async def get_db():
    """
    Async dependency — yields a database session.
    Usage: db: AsyncSession = Depends(get_db)
    """
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Create all tables on startup (dev only). Use Alembic for production."""
    from models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """Dispose engine on shutdown."""
    await engine.dispose()
