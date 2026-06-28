"""
Application configuration — loads from .env file.
Optimized for VPS with limited RAM (aaPanel).
"""

from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # --- Directories ---
    BASE_DIR: Path = Path(__file__).parent.parent.resolve()
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    OUTPUT_DIR: Path = BASE_DIR / "outputs"
    TEMP_DIR: Path = BASE_DIR / "temp"

    # --- Database ---
    # Default: SQLite for local dev. Switch to PostgreSQL for production:
    # DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/video_saas
    DATABASE_URL: str = "sqlite+aiosqlite:///./video_saas.db"

    # --- JWT ---
    JWT_SECRET: str = "CHANGE-THIS-IN-PRODUCTION-super-secret-key-2026"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 30

    # --- Database Pool (for PostgreSQL) ---
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE: int = 1800  # seconds — prevent stale connections

    # --- Bcrypt ---
    BCRYPT_ROUNDS: int = 10  # Lower than default 12 to save CPU on VPS

    # --- App ---
    APP_NAME: str = "Video Affiliate AI Generator — SaaS"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False

    # --- Redis & Celery ---
    REDIS_URL: str = "redis://127.0.0.1:6379/0"
    CELERY_BROKER_URL: str = "redis://127.0.0.1:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://127.0.0.1:6379/0"

    # --- Integrasi agomart (auth terpusat) ---
    # Customer login diverifikasi ke API pusat agomart (email+password+akses tool).
    # Admin/operator app ini tetap login lokal (is_admin=True).
    AGOMART_API_URL: str = "https://agomart.com/api"
    AGOMART_TOOL_SLUG: str = "video"          # slug produk di agomart untuk app ini
    AGOMART_AUTH_ENABLED: bool = True         # set False untuk pakai auth lokal sepenuhnya

    # --- Clip Studio (Auto Klip VIP) ---
    STORAGE_DIR: Path = BASE_DIR / "storage"          # storage/projects/{id}/...
    CLIP_USE_CELERY: bool = False                     # True di VPS (worker celery); False = thread lokal
    CLIP_MAX_SOURCE_SECONDS: int = 7200               # tolak video > 2 jam
    WHISPER_MODEL: str = "small"                      # small utk dev CPU; medium/large-v3 di server kuat
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE: str = "int8"
    ANTHROPIC_API_KEY: str = ""                       # AI curation utama (Claude)
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    PEXELS_API_KEY: str = ""                          # B-roll stock footage (gratis)

    # --- Gateway Generator "Flow" (Gemini teks / Nano Banana gambar / Veo video) ---
    # API gratis buatan sendiri (flowapi.agomart.com). Panggil dari backend saja.
    # Helper: core/gateway_client.py
    GATEWAY_FLOW_URL: str = "https://flowapi.agomart.com"
    GATEWAY_FLOW_KEY: str = ""                         # X-API-Key (isi di .env)
    GATEWAY_FLOW_DOMAIN: str = "revideo.agomart.com"   # utk kuota & log per-domain

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",  # Ignore extra env vars (like DASHSCOPE_API_KEY)
    }


@lru_cache()
def get_settings() -> Settings:
    """Cached settings singleton — loaded once, reused everywhere."""
    return Settings()
