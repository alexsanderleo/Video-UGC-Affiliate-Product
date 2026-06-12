"""
Akses DB sinkron untuk worker pipeline Clip Studio (thread lokal ATAU celery).

Pipeline berat (yt-dlp, whisper, ffmpeg) berjalan sinkron di worker — dipakai
engine sync terpisah (pola sama dengan core/ai_provider.py) supaya tidak
bentrok dengan event loop FastAPI.
"""

import json
import logging
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _sync_url(url: str) -> str:
    return (
        url.replace("+aiosqlite", "")
           .replace("+aiomysql", "+pymysql")
           .replace("+asyncpg", "+psycopg2")
    )


_engine = None
_Session = None


def get_sync_session_factory():
    global _engine, _Session
    if _Session is None:
        url = _sync_url(settings.DATABASE_URL)
        kwargs = {"future": True}
        if "sqlite" not in url:
            kwargs.update({"pool_size": 5, "max_overflow": 5, "pool_recycle": 1800})
        _engine = create_engine(url, **kwargs)
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _Session


@contextmanager
def sync_session():
    Session = get_sync_session_factory()
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_project_progress(project_id: str, status: str = None, percent: int = None,
                            error: str = None, **fields):
    """Update kolom progress project — dipanggil tiap tahapan pipeline (Tahap F)."""
    from models.clipstudio import ClipProject
    with sync_session() as s:
        proj = s.get(ClipProject, project_id)
        if not proj:
            return
        if status is not None:
            proj.status = status
        if percent is not None:
            proj.percent = max(0, min(100, int(percent)))
        if error is not None:
            proj.error_message = error
        for k, v in fields.items():
            if hasattr(proj, k):
                setattr(proj, k, v)
    logger.info("[ClipStudio %s] %s %s%% %s", project_id[:8], status, percent, error or "")


def update_export_progress(export_id: str, status: str = None, percent: int = None,
                           error: str = None, **fields):
    from models.clipstudio import ClipExport
    with sync_session() as s:
        exp = s.get(ClipExport, export_id)
        if not exp:
            return
        if status is not None:
            exp.status = status
        if percent is not None:
            exp.percent = max(0, min(100, int(percent)))
        if error is not None:
            exp.error_message = error
        for k, v in fields.items():
            if hasattr(exp, k):
                setattr(exp, k, v)
