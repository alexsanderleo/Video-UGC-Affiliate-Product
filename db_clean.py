import os
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main():
    # Load DATABASE_URL from .env
    db_url = None
    if os.path.exists(".env"):
        with open(".env") as f:
            for line in f:
                if line.strip().startswith("DATABASE_URL="):
                    db_url = line.strip().split("=", 1)[1]
                    break
    if not db_url:
        db_url = "sqlite+aiosqlite:///./video_saas.db"
    
    # Strip any enclosing quotes
    db_url = db_url.strip("'\"")
    
    # Handle mysql pool pre-ping bug patching if present in URL
    engine_kwargs = {}
    if "mysql" in db_url:
        # Disable pre-ping for mysql to avoid reconnect signature bug
        engine_kwargs["pool_pre_ping"] = False
    
    print(f"Connecting to database: {db_url}")
    try:
        engine = create_async_engine(db_url, **engine_kwargs)
        async with engine.begin() as conn:
            # Fix corrupted expired_at values
            result = await conn.execute(text("UPDATE users SET expired_at = NULL WHERE expired_at = 'monthly';"))
            print(f"[SUCCESS] Database cleaned successfully! Rows updated: {result.rowcount}")
        await engine.dispose()
    except Exception as e:
        print(f"[ERROR] Failed to clean database: {e}")

if __name__ == "__main__":
    asyncio.run(main())
