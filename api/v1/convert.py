"""
Convert endpoint — protected video conversion (quota-free).
Offloads compression processing to Celery queue and streams progress via Redis Pub/Sub.
"""

import uuid
import json
import shutil
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import redis.asyncio as async_redis

from fastapi import APIRouter, Depends, HTTPException, status, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from models.user import User
from models.generation_log import GenerationLog
from core.config import get_settings
from core.tasks import convert_video_task
from core.pipeline import UPLOAD_DIR

router = APIRouter(prefix="/convert", tags=["Video Conversion"])
settings = get_settings()


def sse_event(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post(
    "",
    summary="Convert and compress video (protected, quota-free, queued)",
)
async def convert_video(
    video: UploadFile = File(...),
    crf_level: int = Form(26),  # Default: Balanced (26)
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Convert and compress video. Protected by auth, does NOT consume daily quota.
    
    Workflow:
    1. Register Generation Log with "pending" status (and convert markers)
    2. Trigger Background Celery Task
    3. Connect to Redis Pub/Sub and stream updates via SSE
    """
    # Create job ID and pathing
    job_id = str(uuid.uuid4())[:8]
    
    # Save input video file (accept any format)
    video_ext = Path(video.filename).suffix or '.mp4'
    video_filename = f"{job_id}_input{video_ext}"
    video_path = UPLOAD_DIR / video_filename
    
    with open(video_path, "wb") as buffer:
        shutil.copyfileobj(video.file, buffer)

    # Calculate uploaded file size (ingress bandwidth)
    ingress_bytes = Path(video_path).stat().st_size if Path(video_path).exists() else 0

    # Register in DB with "pending"
    # To keep DB schema consistent, we record this conversion task
    log = GenerationLog(
        job_id=job_id,
        user_id=current_user.id,
        status="pending",
        voice="convert",
        watermark_mode="convert",
        watermark_text=f"crf={crf_level}",
        video_name=video.filename,
        ingress_bytes=ingress_bytes,
    )
    db.add(log)
    await db.commit()

    # Offload execution to Celery Queue
    try:
        convert_video_task.apply_async(
            kwargs={
                'job_id': job_id,
                'video_path': str(video_path),
                'crf_level': crf_level,
                'user_id': current_user.id
            },
            task_id=job_id
        )
    except Exception as e:
        print(f"[WARNING] Celery worker could not be triggered (Redis is probably down): {e}")

    async def pipeline_stream():
        """Subscribe to Redis Pub/Sub channel and yield updates in real-time."""
        r = async_redis.from_url(settings.REDIS_URL)
        pubsub = r.pubsub()
        channel_name = f"task_progress:{job_id}"
        await pubsub.subscribe(channel_name)

        # 1. Yield initial state first if already cached to prevent race conditions
        state_key = f"task_state:{job_id}"
        cached_state = await r.get(state_key)
        
        has_sent_complete = False

        if cached_state:
            try:
                data = json.loads(cached_state)
                yield sse_event(data)
                if data.get('status') in ['complete', 'error']:
                    has_sent_complete = True
            except Exception:
                pass

        if not has_sent_complete:
            try:
                while True:
                    # Non-blocking get_message with timeout to support keep-alive
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                    if message and message.get('type') == 'message':
                        try:
                            data = json.loads(message['data'])
                            yield sse_event(data)
                            if data.get('status') in ['complete', 'error']:
                                break
                        except Exception:
                            pass
                    elif message is None:
                        # Yield keep-alive SSE comment to prevent timeouts
                        yield ": keepalive\n\n"
                    else:
                        await asyncio.sleep(0.1)
            finally:
                # Cleanup connection safely
                try:
                    await pubsub.unsubscribe(channel_name)
                except Exception:
                    pass
                try:
                    await r.close()
                except Exception:
                    pass
        else:
            try:
                await pubsub.unsubscribe(channel_name)
            except Exception:
                pass
            try:
                await r.close()
            except Exception:
                pass

    return StreamingResponse(
        pipeline_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )
