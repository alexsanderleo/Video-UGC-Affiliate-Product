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
    })
    # Only enable pool_pre_ping if it's not mysql+aiomysql to avoid connection.ping() reconnect TypeError bug
    if "mysql" not in settings.DATABASE_URL:
        _engine_kwargs["pool_pre_ping"] = True

from sqlalchemy import event

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

# Connection listener to enforce WAL mode on SQLite
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if "sqlite" in settings.DATABASE_URL:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass
        finally:
            cursor.close()

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

    # Dynamic auto-migration for SQLite to add new columns safely
    if "sqlite" in settings.DATABASE_URL:
        async with engine.begin() as conn:
            def check_and_add_columns(connection):
                from sqlalchemy import inspect, text
                inspector = inspect(connection)
                columns = [c["name"] for c in inspector.get_columns("users")]
                
                if "price_plan" not in columns:
                    connection.execute(text("ALTER TABLE users ADD COLUMN price_plan VARCHAR(50);"))
                if "price" not in columns:
                    connection.execute(text("ALTER TABLE users ADD COLUMN price INTEGER;"))
                if "expired_at" not in columns:
                    connection.execute(text("ALTER TABLE users ADD COLUMN expired_at TIMESTAMP;"))
                
            await conn.run_sync(check_and_add_columns)


async def close_db():
    """Dispose engine on shutdown."""
    await engine.dispose()

