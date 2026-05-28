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
    "/analyze",
    summary="Analyze video and generate AI script draft (protected)",
)
async def analyze_video(
    video: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Step 1 of the video generation pipeline.
    Uploads a video, runs Qwen VL Plus video understanding, and returns parsed copywriting draft (title, narration, hashtags).
    """
    now = datetime.utcnow()

    # Quota check (only check if remaining quota > 0, do not decrement yet!)
    if current_user.quota_reset is None or (now - current_user.quota_reset).total_seconds() > 86400:
        current_user.quota_used = 0
        current_user.quota_reset = now
        db.add(current_user)
        await db.commit()

    if current_user.quota_used >= current_user.daily_quota:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Kuota harian Anda habis ({current_user.daily_quota} video/hari). "
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

    # Send video to Qwen VL Plus for analysis
    try:
        from core.pipeline import get_video_duration, step_a_video_understanding
        # Read video duration via ffprobe
        video_duration = await asyncio.to_thread(get_video_duration, str(video_path))
        video_duration_int = max(5, int(round(video_duration)))

        script = await asyncio.to_thread(
            step_a_video_understanding,
            str(video_path),
            video_duration_int
        )
    except Exception as e:
        print(f"[api/v1/generate/analyze] Error during Qwen: {e}")
        # Fallback narration script
        script = (
            "[JUDUL]\nProduk Keren Viral Terlaris\n\n"
            "[HASHTAG]\n#produkviral #racunshopee #affiliate\n\n"
            "[NARASI]\nHai semuanya! Kamu harus lihat produk keren ini! "
            "Lihat betapa luar biasanya kualitas produk ini, benar-benar amazing! "
            "Wow, coba perhatikan bagian ini — luar biasa kan?! "
            "Tidak heran produk ini sudah viral di mana-mana! "
            "Buruan grab sebelum kehabisan, link ada di bio ya!"
        )

    # Parse script
    from core.tasks import parse_qwen_output
    title, hashtags, narration = parse_qwen_output(script)

    return {
        "status": "success",
        "job_id": job_id,
        "video_filename": video_filename,
        "video_duration": video_duration if 'video_duration' in locals() else 30.0,
        "title": title,
        "narration": narration,
        "hashtags": hashtags,
    }


@router.post(
    "/render",
    summary="Render final video and audio (protected + quota + queued)",
)
async def render_video_endpoint(
    job_id: str = Form(...),
    video_filename: str = Form(...),
    title: str = Form(...),
    narration: str = Form(...),
    hashtags: str = Form(...),
    voice: str = Form("id-ID-GadisNeural"),
    watermark_mode: str = Form("text"),
    watermark_text: str = Form(""),
    watermark_position: str = Form("top-right"),
    watermark_logo: Optional[UploadFile] = File(None),
    sub_font: str = Form("Arial"),
    sub_size: int = Form(26),
    sub_color: str = Form("#FFFF00"),
    sub_sec_color: str = Form("#FFFFFF"),
    sub_opacity: float = Form(1.0),
    wm_opacity: float = Form(0.65),
    use_subtitle: str = Form("true"),
    use_speed_ramping: str = Form("true"),
    use_camera_shake: str = Form("true"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Step 2 of the video generation pipeline.
    Receives edited script text, decrements user daily quota, schedules background rendering, and streams updates.
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
            detail=f"Kuota harian Anda habis ({current_user.daily_quota} video/hari). "
                   f"Reset dalam {24 - int((now - current_user.quota_reset).total_seconds() / 3600)} jam.",
        )

    # Check that input video actually exists
    video_path = UPLOAD_DIR / video_filename
    if not video_path.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File video tidak ditemukan di server. Silakan upload ulang video Anda."
        )

    # Save logo file if uploaded
    logo_path = None
    if watermark_logo:
        logo_filename = f"{job_id}_logo.png"
        logo_path = str(UPLOAD_DIR / logo_filename)
        with open(logo_path, "wb") as buffer:
            shutil.copyfileobj(watermark_logo.file, buffer)

    # Calculate uploaded file sizes (ingress bandwidth)
    ingress_bytes = Path(video_path).stat().st_size if Path(video_path).exists() else 0
    if logo_path and Path(logo_path).exists():
        ingress_bytes += Path(logo_path).stat().st_size

    # Register in DB with "pending"
    log = GenerationLog(
        job_id=job_id,
        user_id=current_user.id,
        status="pending",
        voice=voice,
        watermark_mode=watermark_mode,
        watermark_text=watermark_text or "",
        video_name=video_filename,
        ingress_bytes=ingress_bytes,
    )
    db.add(log)

    # Increment quota immediately upon heavy render
    current_user.quota_used += 1
    db.add(current_user)
    await db.commit()

    # Offload execution to Celery Queue using the NEW task render_video_from_script
    celery_dispatch_failed = False
    celery_error_msg = ""
    try:
        from core.tasks import render_video_from_script_task
        render_video_from_script_task.apply_async(
            kwargs={
                'job_id': job_id,
                'video_path': str(video_path),
                'narration': narration,
                'title': title,
                'hashtags': hashtags,
                'voice': voice,
                'watermark_mode': watermark_mode,
                'watermark_text': watermark_text or "",
                'watermark_position': watermark_position,
                'logo_path': logo_path,
                'user_id': current_user.id,
                'sub_font': sub_font,
                'sub_size': sub_size,
                'sub_color': sub_color,
                'sub_sec_color': sub_sec_color,
                'sub_opacity': sub_opacity,
                'wm_opacity': wm_opacity,
                'use_subtitle': use_subtitle,
                'use_speed_ramping': use_speed_ramping,
                'use_camera_shake': use_camera_shake,
            },
            task_id=job_id
        )
    except Exception as e:
        print(f"[CRITICAL] Celery dispatch FAILED for render job {job_id}: {e}")
        celery_dispatch_failed = True
        celery_error_msg = str(e)
        # Publish error immediately to Redis so SSE doesn't hang forever
        try:
            import redis as sync_redis
            r_sync = sync_redis.from_url(settings.REDIS_URL)
            error_data = json.dumps({'step': 'error', 'status': 'error', 'job_id': job_id, 'message': f'Celery worker tidak dapat memproses task. Error: {celery_error_msg}'}, ensure_ascii=False)
            r_sync.setex(f"task_state:{job_id}", 3600, error_data)
            r_sync.publish(f"task_progress:{job_id}", error_data)
            r_sync.close()
        except Exception:
            pass

    async def pipeline_stream():
        """Subscribe to Redis Pub/Sub channel and yield updates in real-time."""
        r = async_redis.from_url(settings.REDIS_URL)
        pubsub = r.pubsub()
        channel_name = f"task_progress:{job_id}"
        await pubsub.subscribe(channel_name)

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
            import time as _time
            _stream_start = _time.time()
            _MAX_STREAM_SECONDS = 300  # 5 minute max to prevent infinite hang
            try:
                while True:
                    # Safety: abort SSE if streaming for more than 5 minutes
                    if _time.time() - _stream_start > _MAX_STREAM_SECONDS:
                        yield sse_event({'step': 'error', 'status': 'error', 'message': 'Timeout: proses rendering memakan waktu terlalu lama. Silakan coba lagi.'})
                        break
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
                        yield ": keepalive\n\n"
                    else:
                        await asyncio.sleep(0.1)
            finally:
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
    sub_font: str = Form("Arial"),
    sub_size: int = Form(26),
    sub_color: str = Form("#FFFF00"),
    sub_sec_color: str = Form("#FFFFFF"),
    sub_opacity: float = Form(1.0),
    wm_opacity: float = Form(0.65),
    use_subtitle: str = Form("true"),
    use_speed_ramping: str = Form("true"),
    use_camera_shake: str = Form("true"),
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
        
    # Save logo file if uploaded
    logo_path = None
    if watermark_logo:
        logo_filename = f"{job_id}_logo.png"
        logo_path = str(UPLOAD_DIR / logo_filename)
        with open(logo_path, "wb") as buffer:
            shutil.copyfileobj(watermark_logo.file, buffer)

    # Calculate uploaded file sizes (ingress bandwidth)
    ingress_bytes = Path(video_path).stat().st_size if Path(video_path).exists() else 0
    if logo_path and Path(logo_path).exists():
        ingress_bytes += Path(logo_path).stat().st_size

    # Register in DB with "pending"
    log = GenerationLog(
        job_id=job_id,
        user_id=current_user.id,
        status="pending",
        voice=voice,
        watermark_mode=watermark_mode,
        watermark_text=watermark_text or "",
        video_name=video.filename,
        ingress_bytes=ingress_bytes,
    )
    db.add(log)

    # Increment quota immediately
    current_user.quota_used += 1
    db.add(current_user)
    await db.commit()

    # Offload execution to Celery Queue
    try:
        render_video_task.apply_async(
            kwargs={
                'job_id': job_id,
                'video_path': str(video_path),
                'voice': voice,
                'watermark_mode': watermark_mode,
                'watermark_text': watermark_text or "",
                'watermark_position': watermark_position,
                'logo_path': logo_path,
                'user_id': current_user.id,
                'sub_font': sub_font,
                'sub_size': sub_size,
                'sub_color': sub_color,
                'sub_sec_color': sub_sec_color,
                'sub_opacity': sub_opacity,
                'wm_opacity': wm_opacity,
                'use_subtitle': use_subtitle,
                'use_speed_ramping': use_speed_ramping,
                'use_camera_shake': use_camera_shake,
            },
            task_id=job_id
        )
    except Exception as e:
        print(f"[CRITICAL] Celery dispatch FAILED for generate job {job_id}: {e}")
        # Publish error immediately to Redis so SSE doesn't hang forever
        try:
            import redis as sync_redis
            r_sync = sync_redis.from_url(settings.REDIS_URL)
            error_data = json.dumps({'step': 'error', 'status': 'error', 'job_id': job_id, 'message': f'Celery worker tidak dapat memproses task. Error: {str(e)}'}, ensure_ascii=False)
            r_sync.setex(f"task_state:{job_id}", 3600, error_data)
            r_sync.publish(f"task_progress:{job_id}", error_data)
            r_sync.close()
        except Exception:
            pass

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
            import time as _time
            _stream_start = _time.time()
            _MAX_STREAM_SECONDS = 300  # 5 minute max to prevent infinite hang
            try:
                while True:
                    # Safety: abort SSE if streaming for more than 5 minutes
                    if _time.time() - _stream_start > _MAX_STREAM_SECONDS:
                        yield sse_event({'step': 'error', 'status': 'error', 'message': 'Timeout: proses rendering memakan waktu terlalu lama. Silakan coba lagi.'})
                        break
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


@router.post(
    "/cancel",
    summary="Cancel ongoing video generation or conversion task",
)
async def cancel_generation(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel an ongoing task (either generator or converter) by job_id:
    1. Verify current user owns this job
    2. Terminate the active FFmpeg child PID (registered in Redis)
    3. Revoke/terminate the Celery task
    4. Publish failure/error message to SSE Redis channel
    5. Update DB record status to failed
    6. Clean up temporary files
    """
    from sqlalchemy import select
    import redis
    import signal
    import os
    from core.celery_app import celery_app
    from core.pipeline import UPLOAD_DIR, TEMP_DIR, OUTPUT_DIR

    job_id = payload.get("job_id")
    if not job_id:
        raise HTTPException(status_code=400, detail="Missing job_id in payload.")

    # 1. Fetch job log & verify owner
    result = await db.execute(
        select(GenerationLog).where(GenerationLog.job_id == job_id)
    )
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Job not found.")

    if log.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not have permission to abort this job.")

    if log.status not in ["pending", "processing"]:
        return {"status": "success", "message": f"Job is already finished with status '{log.status}'."}

    # 2. Retrieve child FFmpeg PID from Redis and kill it
    r_sync = redis.from_url(settings.REDIS_URL)
    pid_key = f"task_pid:{job_id}"
    pid_bytes = r_sync.get(pid_key)
    if pid_bytes:
        try:
            pid = int(pid_bytes.decode('utf-8'))
            print(f"[CANCEL] Killing FFmpeg subprocess PID {pid} for job {job_id}")
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # Already dead
        except Exception as ex:
            print(f"[CANCEL] Error killing PID: {ex}")
        finally:
            r_sync.delete(pid_key)

    # 3. Publish error/cancel event to SSE Pub/Sub
    channel = f"task_progress:{job_id}"
    cancel_event = {
        "step": "error",
        "status": "error",
        "message": "Proses dibatalkan oleh pengguna."
    }
    r_sync.publish(channel, json.dumps(cancel_event, ensure_ascii=False))
    r_sync.close()

    # 4. Revoke the Celery task
    try:
        celery_app.control.revoke(job_id, terminate=True, signal='SIGKILL')
        print(f"[CANCEL] Revoked Celery task {job_id}")
    except Exception as ce:
        print(f"[CANCEL] Error revoking task: {ce}")

    # 5. Update DB log status to failed
    log.status = "failed"
    log.error_message = "Proses dibatalkan oleh pengguna."
    await db.commit()

    # 6. Clean up temporary files associated with this job_id
    for directory in [UPLOAD_DIR, TEMP_DIR, OUTPUT_DIR]:
        for filepath in directory.glob(f"{job_id}*"):
            try:
                if filepath.exists():
                    filepath.unlink()
                    print(f"[CANCEL] Deleted temp file: {filepath.name}")
            except Exception as fe:
                print(f"[CANCEL] Error deleting file {filepath.name}: {fe}")

    return {"status": "success", "message": "Proses rendering dan konversi berhasil dibatalkan."}
