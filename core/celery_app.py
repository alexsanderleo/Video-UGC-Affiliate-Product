"""
Celery configuration module.
Enforces worker_concurrency=4 limit to optimize low-RAM aaPanel VPS.
"""

from celery import Celery
import sys
from pathlib import Path

# Programmatically append project root to sys.path for server path resolution
BASE_DIR = Path(__file__).parent.parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Force-remove any conflicting third-party 'models' package from sys.modules
if 'models' in sys.modules:
    try:
        import models
        if not hasattr(models, 'GenerationLog'):
            del sys.modules['models']
    except Exception:
        del sys.modules['models']

from core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "video_automation",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_concurrency=4,  # Strictly enforce maximum 4 concurrent processes
    task_track_started=True,
)

# Auto-discover tasks inside core module
celery_app.autodiscover_tasks(["core"])

# Alias 'app' for easy auto-discovery by Celery CLI
app = celery_app
