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

    # Dynamic auto-migration for all databases to add new columns safely
    async with engine.begin() as conn:
        def check_and_add_columns(connection):
            from sqlalchemy import inspect, text
            inspector = inspect(connection)
            
            # Check users table
            columns = [c["name"] for c in inspector.get_columns("users")]
            
            # Detect dialect
            is_mysql = "mysql" in connection.dialect.name
            
            if "price_plan" not in columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN price_plan VARCHAR(50);"))
            if "price" not in columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN price INTEGER;"))
            if "expired_at" not in columns:
                if is_mysql:
                    connection.execute(text("ALTER TABLE users ADD COLUMN expired_at DATETIME NULL;"))
                else:
                    connection.execute(text("ALTER TABLE users ADD COLUMN expired_at TIMESTAMP;"))
            if "is_admin" not in columns:
                if is_mysql:
                    connection.execute(text("ALTER TABLE users ADD COLUMN is_admin TINYINT(1) DEFAULT 0 NOT NULL;"))
                else:
                    connection.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0 NOT NULL;"))
            
            # Check generation_logs table
            gen_columns = [c["name"] for c in inspector.get_columns("generation_logs")]
            if "video_name" not in gen_columns:
                connection.execute(text("ALTER TABLE generation_logs ADD COLUMN video_name VARCHAR(255) NULL;"))
            if "ingress_bytes" not in gen_columns:
                connection.execute(text("ALTER TABLE generation_logs ADD COLUMN ingress_bytes BIGINT DEFAULT 0;"))
            
        await conn.run_sync(check_and_add_columns)

    # Seed default admin user if not exists
    from sqlalchemy import select
    from models.user import User
    from core.security import hash_password
    from sqlalchemy.exc import IntegrityError
    async with async_session() as session:
        result = await session.execute(select(User).where(User.email == "admin@gmail.com"))
        if result.scalar_one_or_none() is None:
            admin_user = User(
                email="admin@gmail.com",
                hashed_pw=hash_password("admin123456"),
                full_name="System Admin",
                is_active=True,
                is_verified=True,
                is_admin=True,
                daily_quota=999,
                price_plan="1year",
                price=1998000,
                token_version=0
            )
            session.add(admin_user)
            try:
                await session.commit()
                print("[SEED] Created default admin user: admin@gmail.com / admin123456")
            except IntegrityError:
                await session.rollback()
                print("[SEED] Admin user already created by another worker process.")


async def close_db():
    """Dispose engine on shutdown."""
    await engine.dispose()

