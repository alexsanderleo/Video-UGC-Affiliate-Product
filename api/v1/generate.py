"""
Generate endpoint — protected video generation (quota-limited).
Offloads heavy processing to Celery queue and streams progress via Redis Pub/Sub.
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
from core.tasks import render_video_task
from core.pipeline import UPLOAD_DIR

router = APIRouter(prefix="/generate", tags=["Video Generation"])
settings = get_settings()


def sse_event(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post(
    "",
    summary="Generate video affiliate (protected + quota + queued)",
)
async def generate_video(
    video: UploadFile = File(...),
    voice: str = Form("id-ID-GadisNeural"),
    watermark_mode: str = Form("text"),
    watermark_text: str = Form(""),
    watermark_position: str = Form("top-right"),
    watermark_logo: Optional[UploadFile] = File(None),
    task_id: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate video affiliate with daily quota check, Celery queuing, and real-time streaming progress.
    
    Workflow:
    1. Quota Check & Auto-Reset
    2. Register Generation Log with "pending" status
    3. Trigger Background Celery Task
    4. Connect to Redis Pub/Sub and stream updates via SSE
    """
    now = datetime.utcnow()

    # 1. Quota Check & Auto-Reset
    if current_user.quota_reset is None or (now - current_user.quota_reset).total_seconds() > 86400:
        current_user.quota_used = 0
        current_user.quota_reset = now
        db.add(current_user)
        await db.commit()

    if current_user.quota_used >= current_user.daily_quota:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Kuota harian habis ({current_user.daily_quota} video/hari). "
                   f"Reset dalam {24 - int((now - current_user.quota_reset).total_seconds() / 3600)} jam.",
        )

    # Create job ID and pathing
    job_id = str(uuid.uuid4())[:8]
    
    # Save input video file
    video_ext = Path(video.filename).suffix or '.mp4'
    video_filename = f"{job_id}_input{video_ext}"
    video_path = UPLOAD_DIR / video_filename
    
    with open(video_path, "wb") as buffer:
        shutil.copyfileobj(video.file, buffer)
        
    # Save logo file if logo mode is chosen
    logo_path = None
    if watermark_mode == "logo" and watermark_logo:
        logo_filename = f"{job_id}_logo.png"
        logo_path = str(UPLOAD_DIR / logo_filename)
        with open(logo_path, "wb") as buffer:
            shutil.copyfileobj(watermark_logo.file, buffer)

    # Register in DB with "pending"
    log = GenerationLog(
        job_id=job_id,
        user_id=current_user.id,
        status="pending",
        voice=voice,
        watermark_mode=watermark_mode,
        watermark_text=watermark_text or "",
    )
    db.add(log)

    # Increment quota immediately
    current_user.quota_used += 1
    db.add(current_user)
    await db.commit()

    # Offload execution to Celery Queue
    try:
        render_video_task.delay(
            job_id=job_id,
            video_path=str(video_path),
            voice=voice,
            watermark_mode=watermark_mode,
            watermark_text=watermark_text or "",
            watermark_position=watermark_position,
            logo_path=logo_path,
            user_id=current_user.id
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
                    # Non-blocking get_message with timeout to support periodic keep-alive comments
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
                        # Yield keep-alive SSE comment to prevent Nginx, Cloudflare, and HTTP/2 timeouts
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
