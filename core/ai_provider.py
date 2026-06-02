"""
Pembaca konfigurasi provider AI untuk pipeline (konteks SYNC / Celery).

Pipeline `step_a_video_understanding` berjalan sinkron (Celery worker /
asyncio.to_thread), sedangkan aplikasi utama memakai driver async (aiomysql).
Modul ini membuat engine SQLAlchemy **sinkron** terpisah (mysql+pymysql) khusus
untuk membaca tabel `ai_providers` & `app_settings`, lalu mengembalikan provider
yang sedang aktif (mode tunggal) atau berikutnya (mode rotasi).

Selalu aman: bila tabel kosong / error / belum dikonfigurasi, fungsi jatuh ke
konfigurasi Qwen dari .env sehingga proses generate tidak pernah putus.
"""

import logging
import os

from sqlalchemy import create_engine, text

from core.config import get_settings

logger = logging.getLogger("ai_provider")
settings = get_settings()

# Nilai fallback (perilaku lama, sebelum panel admin dikonfigurasi).
_FALLBACK_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
_FALLBACK_MODEL = "qwen-vl-plus"


def _sync_database_url() -> str:
    """Ubah DATABASE_URL async menjadi sinkron untuk PyMySQL/sqlite."""
    url = settings.DATABASE_URL
    return (
        url.replace("+aiomysql", "+pymysql")
        .replace("+asyncpg", "+psycopg2")
        .replace("+aiosqlite", "")
    )


# Engine sinkron dibuat sekali (lazy) dan dipakai ulang.
_sync_engine = None


def _engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(
            _sync_database_url(),
            pool_pre_ping=True,
            pool_recycle=settings.DB_POOL_RECYCLE,
            future=True,
        )
    return _sync_engine


def _fallback_config() -> dict:
    """Konfigurasi Qwen dari .env — dipakai bila DB belum/ tidak bisa dibaca."""
    return {
        "label": "Qwen (.env fallback)",
        "adapter": "openai_video",
        "base_url": _FALLBACK_BASE_URL,
        "model": _FALLBACK_MODEL,
        "api_key": os.getenv("DASHSCOPE_API_KEY", ""),
        "input_type": "video",
    }


def _row_to_config(row) -> dict:
    return {
        "label": row.label,
        "adapter": row.adapter,
        "base_url": row.base_url,
        "model": row.model,
        "api_key": row.api_key,
        "input_type": row.input_type,
    }


def get_active_ai_config() -> dict:
    """Kembalikan konfigurasi provider AI yang harus dipakai untuk generate ini.

    - Rotasi OFF -> baris is_active=True (yang is_enabled).
    - Rotasi ON  -> round-robin antar baris is_enabled (pakai cursor di app_settings).
    - Fallback   -> Qwen dari .env bila tabel kosong / tidak ada baris valid / error.
    """
    try:
        with _engine().begin() as conn:
            rotation = conn.execute(
                text("SELECT value FROM app_settings WHERE `key` = 'ai_rotation_enabled'")
            ).scalar()
            rotation_on = str(rotation) == "1"

            if rotation_on:
                rows = conn.execute(
                    text(
                        "SELECT id, label, adapter, base_url, model, api_key, input_type "
                        "FROM ai_providers WHERE is_enabled = 1 ORDER BY sort_order, id"
                    )
                ).all()
                if not rows:
                    return _fallback_config()
                cursor = conn.execute(
                    text("SELECT value FROM app_settings WHERE `key` = 'ai_rotation_cursor'")
                ).scalar()
                idx = (int(cursor) if cursor and str(cursor).isdigit() else 0) % len(rows)
                next_idx = (idx + 1) % len(rows)
                conn.execute(
                    text(
                        "INSERT INTO app_settings (`key`, value) VALUES ('ai_rotation_cursor', :v) "
                        "ON DUPLICATE KEY UPDATE value = :v"
                    ),
                    {"v": str(next_idx)},
                )
                return _row_to_config(rows[idx])

            # Mode tunggal: provider aktif terpilih.
            row = conn.execute(
                text(
                    "SELECT id, label, adapter, base_url, model, api_key, input_type "
                    "FROM ai_providers WHERE is_active = 1 AND is_enabled = 1 "
                    "ORDER BY sort_order, id LIMIT 1"
                )
            ).first()
            if row is None:
                return _fallback_config()
            return _row_to_config(row)
    except Exception as exc:
        logger.warning("Gagal baca config AI provider dari DB, pakai fallback .env: %r", exc)
        return _fallback_config()
