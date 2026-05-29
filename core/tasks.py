"""
Celery tasks module.
Handles video generation pipeline offloaded to background workers.
"""

import sys
from pathlib import Path
from typing import Optional

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

import asyncio
import json
import re
from datetime import datetime, timezone
import redis
from celery import shared_task
from sqlalchemy import select

from core.config import get_settings
from core.database import async_session
from core.pipeline import (
    get_video_duration, get_audio_duration,
    step_a_video_understanding, step_b_tts,
    generate_srt, step_c_ffmpeg,
    clean_script_for_tts, ensure_backsound,
    step_ffmpeg_compress
)
from models.generation_log import GenerationLog

settings = get_settings()

# Sync Redis client for worker
r_client = redis.from_url(settings.REDIS_URL)


def publish_progress(job_id: str, data: dict):
    """Publish progress event to Redis Pub/Sub and save cache state."""
    channel = f"task_progress:{job_id}"
    state_key = f"task_state:{job_id}"
    data["job_id"] = job_id  # Inject job_id for frontend tracking and cancel operations
    # Cache the latest state for 1 hour to prevent race conditions
    r_client.setex(state_key, 3600, json.dumps(data, ensure_ascii=False))
    # Publish to real-time subscribers
    r_client.publish(channel, json.dumps(data, ensure_ascii=False))


def parse_qwen_output(text: str):
    """
    Parses Qwen structured output into Title, Hashtags, and Narration.
    Supports formats:
    [JUDUL] ... [HASHTAG] ... [NARASI] ...
    or fallback if tags are missing.
    """
    title = "Video Affiliate UGC"
    hashtags = "#videoviral #affiliateproduct #racunshopee"
    narration = text
    
    # Try parsing using [JUDUL], [HASHTAG], [NARASI]
    title_match = re.search(r'\[(?:JUDUL|TITLE)\]\s*(.*?)(?=\n\s*\[|\r\n\s*\[|$)', text, re.IGNORECASE | re.DOTALL)
    hashtags_match = re.search(r'\[(?:HASHTAG|TAGS)\]\s*(.*?)(?=\n\s*\[|\r\n\s*\[|$)', text, re.IGNORECASE | re.DOTALL)
    narration_match = re.search(r'\[(?:NARASI|SCRIPT|CONTENT)\]\s*(.*?)(?=\n\s*\[|\r\n\s*\[|$)', text, re.IGNORECASE | re.DOTALL)
    
    if title_match:
        title = title_match.group(1).strip()
    if hashtags_match:
        hashtags = hashtags_match.group(1).strip()
    if narration_match:
        narration = narration_match.group(1).strip()
    else:
        # Fallback if no tags are present (legacy behaviour)
        # Clean tags from narration if some exist
        cleaned = re.sub(r'\[(?:JUDUL|TITLE|HASHTAG|TAGS|NARASI|SCRIPT|CONTENT)\]', '', text, flags=re.IGNORECASE)
        narration = cleaned.strip()
        
    return title, hashtags, narration


def slugify_filename(title: str) -> str:
    """Convert title to a safe lowercase filename slug."""
    s = title.strip().lower()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s-]+', '_', s)
    return s[:50].strip('_')


@shared_task(name="core.tasks.render_video")
def render_video_task(
    job_id: str,
    video_path: str,
    voice: str,
    watermark_mode: str,
    watermark_text: str,
    watermark_position: str,
    logo_path: Optional[str],
    user_id: int,
    sub_font: str = "Impact",
    sub_size: int = 45,
    sub_color: str = "#F5CC00",
    sub_sec_color: str = "#FFFFFF",
    sub_opacity: float = 1.0,
    wm_opacity: float = 0.65,
    use_subtitle: str = "true",
    use_speed_ramping: str = "true",
    use_camera_shake: str = "false",
    thumbnail_path: Optional[str] = None,
    backsound_path: Optional[str] = None,
    backsound_volume: float = 0.12,
    video_volume: float = 0.0,
):
    """Celery task to run the video generation pipeline in a background worker."""
    return asyncio.run(
        async_render_video(
            job_id, video_path, voice, watermark_mode,
            watermark_text, watermark_position, logo_path, user_id,
            sub_font, sub_size, sub_color, sub_sec_color, sub_opacity, wm_opacity,
            use_subtitle, use_speed_ramping, use_camera_shake, thumbnail_path,
            backsound_path, backsound_volume, video_volume
        )
    )


@shared_task(name="core.tasks.render_video_from_script")
def render_video_from_script_task(
    job_id: str,
    video_path: str,
    narration: str,
    title: str,
    hashtags: str,
    voice: str,
    watermark_mode: str,
    watermark_text: str,
    watermark_position: str,
    logo_path: Optional[str],
    user_id: int,
    sub_font: str = "Impact",
    sub_size: int = 45,
    sub_color: str = "#F5CC00",
    sub_sec_color: str = "#FFFFFF",
    sub_opacity: float = 1.0,
    wm_opacity: float = 0.65,
    use_subtitle: str = "true",
    use_speed_ramping: str = "true",
    use_camera_shake: str = "false",
    thumbnail_path: Optional[str] = None,
    backsound_path: Optional[str] = None,
    backsound_volume: float = 0.12,
    video_volume: float = 0.0,
):
    """Celery task to run the video generation pipeline with an already generated/edited narration script."""
    return asyncio.run(
        async_render_video_from_script(
            job_id, video_path, narration, title, hashtags, voice, watermark_mode,
            watermark_text, watermark_position, logo_path, user_id,
            sub_font, sub_size, sub_color, sub_sec_color, sub_opacity, wm_opacity,
            use_subtitle, use_speed_ramping, use_camera_shake, thumbnail_path,
            backsound_path, backsound_volume, video_volume
        )
    )


async def async_render_video_from_script(
    job_id: str,
    video_path: str,
    narration: str,
    title: str,
    hashtags: str,
    voice: str,
    watermark_mode: str,
    watermark_text: str,
    watermark_position: str,
    logo_path: Optional[str],
    user_id: int,
    sub_font: str = "Impact",
    sub_size: int = 45,
    sub_color: str = "#F5CC00",
    sub_sec_color: str = "#FFFFFF",
    sub_opacity: float = 1.0,
    wm_opacity: float = 0.65,
    use_subtitle: str = "true",
    use_speed_ramping: str = "true",
    use_camera_shake: str = "false",
    thumbnail_path: Optional[str] = None,
    backsound_path: Optional[str] = None,
    backsound_volume: float = 0.12,
    video_volume: float = 0.0,
):
    """Async pipeline implementation called inside Celery worker using edited narration script."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    
    _engine_kwargs = {"echo": settings.DEBUG, "future": True}
    if "sqlite" not in settings.DATABASE_URL:
        _engine_kwargs["poolclass"] = NullPool
        
    task_engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)
    task_session = async_sessionmaker(task_engine, class_=AsyncSession, expire_on_commit=False)

    tts_path = None
    srt_path = None
    output_path = None
    start_time = datetime.now(timezone.utc)

    async def update_log(status_str: str, duration: float = 0.0, bandwidth: int = 0, error: str = None, video_name: str = None):
        """Helper to update logging database record."""
        async with task_session() as session:
            result = await session.execute(
                select(GenerationLog).where(GenerationLog.job_id == job_id)
            )
            log = result.scalar_one_or_none()
            if log:
                log.status = status_str
                if video_name:
                    log.video_name = video_name
                if duration > 0:
                    log.duration = duration
                if bandwidth > 0:
                    log.bandwidth_bytes = bandwidth
                if error:
                    log.error_message = error
                await session.commit()

    try:
        await update_log("processing")
        
        friendly_slug = slugify_filename(title)
        friendly_filename = f"{friendly_slug}_output_final.mp4" if friendly_slug else f"{job_id}_output_final.mp4"

        # --- Step B: Edge-TTS ---
        publish_progress(job_id, {'step': 'B_start', 'status': 'processing'})

        # Clean script
        tts_text = clean_script_for_tts(narration)
        if '[' in tts_text and 'Catatan' in tts_text:
            tts_text = re.sub(r'\[.*?Catatan.*?\]', '', tts_text).strip()
        if not tts_text:
            tts_text = "Hai semuanya! Produk ini luar biasa, buruan cek sekarang!"

        is_sub = str(use_subtitle).lower() == 'true'

        tts_filename = f"{job_id}_narasi.mp3"
        tts_path = settings.TEMP_DIR / tts_filename
        srt_path = None
        if is_sub:
            srt_filename = f"{job_id}_subtitles.ass"
            srt_path = settings.TEMP_DIR / srt_filename

        # Call Edge-TTS asynchronously and generate subtitles simultaneously
        await step_b_tts(
            tts_text, voice, str(tts_path), srt_path=str(srt_path) if srt_path else None,
            sub_font=sub_font, sub_size=sub_size, sub_color=sub_color,
            sub_sec_color=sub_sec_color, sub_opacity=sub_opacity
        )
        publish_progress(job_id, {'step': 'B_done', 'status': 'done'})

        # --- Subtitles (Audio-Driven Sync) ---
        audio_duration = await asyncio.to_thread(get_audio_duration, str(tts_path))

        # Generate subtitle SRT file
        if is_sub and srt_path:
            await asyncio.to_thread(
                generate_srt, tts_text, audio_duration, str(srt_path),
                sub_font=sub_font, sub_size=sub_size, sub_color=sub_color,
                sub_sec_color=sub_sec_color, sub_opacity=sub_opacity
            )

        # --- Step C: FFmpeg blend with anti-copyright ---
        publish_progress(job_id, {'step': 'C_start', 'status': 'processing'})

        # Resolve backsound
        backsound = None
        if backsound_path == "default":
            backsound = await asyncio.to_thread(ensure_backsound)
        elif backsound_path and backsound_path != "none":
            backsound = backsound_path

        output_filename = f"{job_id}_output_final.mp4"
        output_path = settings.OUTPUT_DIR / output_filename

        # Render video
        await asyncio.to_thread(
            step_c_ffmpeg,
            input_video=video_path,
            tts_audio=str(tts_path),
            backsound=backsound,
            watermark_text=watermark_text,
            watermark_mode=watermark_mode,
            watermark_logo=logo_path,
            output_path=str(output_path),
            watermark_position=watermark_position,
            subtitle_path=str(srt_path) if srt_path else None,
            job_id=job_id,
            sub_font=sub_font,
            sub_size=sub_size,
            sub_color=sub_color,
            sub_sec_color=sub_sec_color,
            sub_opacity=sub_opacity,
            wm_opacity=wm_opacity,
            use_speed_ramping=use_speed_ramping,
            use_camera_shake=use_camera_shake,
            thumbnail_path=thumbnail_path,
            backsound_volume=backsound_volume,
            video_volume=video_volume,
        )

        publish_progress(job_id, {'step': 'C_done', 'status': 'done'})

        # Calculate bandwidth metrics
        bandwidth_bytes = Path(output_path).stat().st_size if Path(output_path).exists() else 0

        # --- Complete ---
        publish_progress(job_id, {
            'step': 'complete',
            'status': 'complete',
            'video_url': f'/outputs/{output_filename}',
            'filename': output_filename,
            'friendly_filename': friendly_filename,
            'caption': f"[JUDUL]\n{title}\n\n[HASHTAG]\n{hashtags}\n\n[NARASI]\n{narration}",
            'title': title,
            'hashtags': hashtags,
            'narration': narration
        })

        await update_log(
            status_str="success",
            duration=audio_duration,
            bandwidth=bandwidth_bytes,
            video_name=output_filename
        )

    except Exception as e:
        error_msg = str(e)
        err_lower = error_msg.lower()
        if "moov atom" in err_lower or "invalid data found" in err_lower or "low score of 1" in err_lower:
            error_msg = (
                "Gagal memproses video Anda karena file terputus saat diunggah (corrupt atau tidak utuh). "
                "Silakan periksa koneksi internet Anda, pastikan video dapat diputar dengan normal di HP/PC, "
                "lalu coba lakukan upload ulang."
            )
        publish_progress(job_id, {
            'step': 'error',
            'status': 'error',
            'message': error_msg
        })
        await update_log(
            status_str="failed",
            error=error_msg
        )
    finally:
        # Dispose the local engine to clean up connection resources bound to this event loop
        try:
            await task_engine.dispose()
        except Exception:
            pass

        # Secure cleanups of intermediate media files
        for p in [video_path, tts_path, srt_path]:
            if p and Path(p).exists():
                try:
                    Path(p).unlink()
                except Exception:
                    pass

        # Clean up logo if upload was present
        if logo_path and Path(logo_path).exists():
            try:
                Path(logo_path).unlink()
            except Exception:
                pass

        # Clean up thumbnail if upload was present
        if thumbnail_path and Path(thumbnail_path).exists():
            try:
                Path(thumbnail_path).unlink()
            except Exception:
                pass

        # Clean up custom backsound ONLY if it is job-specific (saved in uploads)
        if backsound_path and Path(backsound_path).exists() and f"{job_id}_" in Path(backsound_path).name:
            try:
                Path(backsound_path).unlink()
            except Exception:
                pass


async def async_render_video(
    job_id: str,
    video_path: str,
    voice: str,
    watermark_mode: str,
    watermark_text: str,
    watermark_position: str,
    logo_path: Optional[str],
    user_id: int,
    sub_font: str = "Impact",
    sub_size: int = 45,
    sub_color: str = "#F5CC00",
    sub_sec_color: str = "#FFFFFF",
    sub_opacity: float = 1.0,
    wm_opacity: float = 0.65,
    use_subtitle: str = "true",
    use_speed_ramping: str = "true",
    use_camera_shake: str = "false",
    thumbnail_path: Optional[str] = None,
    backsound_path: Optional[str] = None,
    backsound_volume: float = 0.12,
    video_volume: float = 0.0,
):
    """Async pipeline implementation called inside the Celery worker."""
    # Construct a local engine bound to the current task's event loop to prevent "attached to a different loop" errors
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    
    _engine_kwargs = {"echo": settings.DEBUG, "future": True}
    if "sqlite" not in settings.DATABASE_URL:
        _engine_kwargs["poolclass"] = NullPool
        
    task_engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)
    task_session = async_sessionmaker(task_engine, class_=AsyncSession, expire_on_commit=False)

    tts_path = None
    srt_path = None
    output_path = None
    start_time = datetime.now(timezone.utc)

    async def update_log(status_str: str, duration: float = 0.0, bandwidth: int = 0, error: str = None, video_name: str = None):
        """Helper to update logging database record."""
        async with task_session() as session:
            result = await session.execute(
                select(GenerationLog).where(GenerationLog.job_id == job_id)
            )
            log = result.scalar_one_or_none()
            if log:
                log.status = status_str
                if video_name:
                    log.video_name = video_name
                if duration > 0:
                    log.duration = duration
                if bandwidth > 0:
                    log.bandwidth_bytes = bandwidth
                if error:
                    log.error_message = error
                await session.commit()

    try:
        # --- Step A: Qwen VL Plus ---
        publish_progress(job_id, {'step': 'A_start', 'status': 'processing'})
        await update_log("processing")

        # Read video duration via ffprobe
        video_duration = await asyncio.to_thread(get_video_duration, video_path)
        video_duration_int = max(5, int(round(video_duration)))

        try:
            # Call DashScope Qwen VL Plus API
            script = await asyncio.to_thread(
                step_a_video_understanding,
                video_path,
                video_duration_int
            )
        except Exception as e:
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

        # Parse Qwen output into Title, Hashtags, Narration
        title, hashtags, narration = parse_qwen_output(script)
        friendly_slug = slugify_filename(title)
        friendly_filename = f"{friendly_slug}_output_final.mp4" if friendly_slug else f"{job_id}_output_final.mp4"

        publish_progress(job_id, {
            'step': 'A_done',
            'status': 'done',
            'script_preview': narration[:150] + '...' if len(narration) > 150 else narration,
            'title': title,
            'hashtags': hashtags,
            'narration': narration
        })

        # --- Step B: Edge-TTS ---
        publish_progress(job_id, {'step': 'B_start', 'status': 'processing'})

        # Clean script
        tts_text = clean_script_for_tts(narration)
        if '[' in tts_text and 'Catatan' in tts_text:
            tts_text = re.sub(r'\[.*?Catatan.*?\]', '', tts_text).strip()
        if not tts_text:
            tts_text = "Hai semuanya! Produk ini luar biasa, buruan cek sekarang!"

        is_sub = str(use_subtitle).lower() == 'true'

        tts_filename = f"{job_id}_narasi.mp3"
        tts_path = settings.TEMP_DIR / tts_filename
        srt_path = None
        if is_sub:
            srt_filename = f"{job_id}_subtitles.ass"
            srt_path = settings.TEMP_DIR / srt_filename

        # Call Edge-TTS asynchronously and generate subtitles simultaneously
        await step_b_tts(
            tts_text, voice, str(tts_path), srt_path=str(srt_path) if srt_path else None,
            sub_font=sub_font, sub_size=sub_size, sub_color=sub_color,
            sub_sec_color=sub_sec_color, sub_opacity=sub_opacity
        )
        publish_progress(job_id, {'step': 'B_done', 'status': 'done'})

        # --- Subtitles (Audio-Driven Sync) ---
        audio_duration = await asyncio.to_thread(get_audio_duration, str(tts_path))

        # Generate subtitle SRT file (will skip because already created by step_b_tts, otherwise generates styled ASS fallback)
        if is_sub and srt_path:
            await asyncio.to_thread(
                generate_srt, tts_text, audio_duration, str(srt_path),
                sub_font=sub_font, sub_size=sub_size, sub_color=sub_color,
                sub_sec_color=sub_sec_color, sub_opacity=sub_opacity
            )

        # --- Step C: FFmpeg blend with anti-copyright ---
        publish_progress(job_id, {'step': 'C_start', 'status': 'processing'})

        # Resolve backsound
        backsound = None
        if backsound_path == "default":
            backsound = await asyncio.to_thread(ensure_backsound)
        elif backsound_path and backsound_path != "none":
            backsound = backsound_path

        output_filename = f"{job_id}_output_final.mp4"
        output_path = settings.OUTPUT_DIR / output_filename

        # Render video
        await asyncio.to_thread(
            step_c_ffmpeg,
            input_video=video_path,
            tts_audio=str(tts_path),
            backsound=backsound,
            watermark_text=watermark_text,
            watermark_mode=watermark_mode,
            watermark_logo=logo_path,
            output_path=str(output_path),
            watermark_position=watermark_position,
            subtitle_path=str(srt_path) if srt_path else None,
            job_id=job_id,
            sub_font=sub_font,
            sub_size=sub_size,
            sub_color=sub_color,
            sub_sec_color=sub_sec_color,
            sub_opacity=sub_opacity,
            wm_opacity=wm_opacity,
            use_speed_ramping=use_speed_ramping,
            use_camera_shake=use_camera_shake,
            thumbnail_path=thumbnail_path,
            backsound_volume=backsound_volume,
            video_volume=video_volume,
        )

        publish_progress(job_id, {'step': 'C_done', 'status': 'done'})

        # Calculate bandwidth metrics
        bandwidth_bytes = Path(output_path).stat().st_size if Path(output_path).exists() else 0

        # --- Complete ---
        publish_progress(job_id, {
            'step': 'complete',
            'status': 'complete',
            'video_url': f'/outputs/{output_filename}',
            'filename': output_filename,
            'friendly_filename': friendly_filename,
            'caption': script,
            'title': title,
            'hashtags': hashtags,
            'narration': narration
        })

        await update_log(
            status_str="success",
            duration=audio_duration,
            bandwidth=bandwidth_bytes,
            video_name=output_filename
        )

    except Exception as e:
        error_msg = str(e)
        err_lower = error_msg.lower()
        if "moov atom" in err_lower or "invalid data found" in err_lower or "low score of 1" in err_lower:
            error_msg = (
                "Gagal memproses video Anda karena file terputus saat diunggah (corrupt atau tidak utuh). "
                "Silakan periksa koneksi internet Anda, pastikan video dapat diputar dengan normal di HP/PC, "
                "lalu coba lakukan upload ulang."
            )
        publish_progress(job_id, {
            'step': 'error',
            'status': 'error',
            'message': error_msg
        })
        await update_log(
            status_str="failed",
            error=error_msg
        )
    finally:
        # Dispose the local engine to clean up connection resources bound to this event loop
        try:
            await task_engine.dispose()
        except Exception:
            pass

        # Secure cleanups of intermediate media files
        for p in [video_path, tts_path, srt_path]:
            if p and Path(p).exists():
                try:
                    Path(p).unlink()
                except Exception:
                    pass

        # Clean up logo if upload was present
        if logo_path and Path(logo_path).exists():
            try:
                Path(logo_path).unlink()
            except Exception:
                pass

        # Clean up thumbnail if upload was present
        if thumbnail_path and Path(thumbnail_path).exists():
            try:
                Path(thumbnail_path).unlink()
            except Exception:
                pass

        # Clean up custom backsound ONLY if it is job-specific (saved in uploads)
        if backsound_path and Path(backsound_path).exists() and f"{job_id}_" in Path(backsound_path).name:
            try:
                Path(backsound_path).unlink()
            except Exception:
                pass


@shared_task(name="core.tasks.convert_video")
def convert_video_task(
    job_id: str,
    video_path: str,
    crf_level: int,
    user_id: int,
):
    """Celery task to run the video conversion & compression in background."""
    return asyncio.run(
        async_convert_video(
            job_id, video_path, crf_level, user_id
        )
    )


async def async_convert_video(
    job_id: str,
    video_path: str,
    crf_level: int,
    user_id: int,
):
    """Async pipeline implementation called inside Celery worker for video compression."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from sqlalchemy.pool import NullPool
    
    _engine_kwargs = {"echo": settings.DEBUG, "future": True}
    if "sqlite" not in settings.DATABASE_URL:
        _engine_kwargs["poolclass"] = NullPool
        
    task_engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)
    task_session = async_sessionmaker(task_engine, class_=AsyncSession, expire_on_commit=False)

    output_path = None
    original_size = Path(video_path).stat().st_size if Path(video_path).exists() else 0

    async def update_log(status_str: str, duration: float = 0.0, bandwidth: int = 0, error: str = None, video_name: str = None):
        """Helper to update logging database record."""
        async with task_session() as session:
            result = await session.execute(
                select(GenerationLog).where(GenerationLog.job_id == job_id)
            )
            log = result.scalar_one_or_none()
            if log:
                log.status = status_str
                if video_name:
                    log.video_name = video_name
                if duration > 0:
                    log.duration = duration
                if bandwidth > 0:
                    log.bandwidth_bytes = bandwidth
                if error:
                    log.error_message = error
                await session.commit()

    try:
        # --- C_start ---
        publish_progress(job_id, {'step': 'C_start', 'status': 'processing'})
        await update_log("processing")

        # Read video duration via ffprobe
        video_duration = await asyncio.to_thread(get_video_duration, video_path)

        output_filename = f"{job_id}_output_final.mp4"
        output_path = settings.OUTPUT_DIR / output_filename

        publish_progress(job_id, {'step': 'C_progress', 'percent': 10})
        
        # Run conversion & compression
        await asyncio.to_thread(
            step_ffmpeg_compress,
            input_video=video_path,
            output_path=str(output_path),
            crf=crf_level,
            job_id=job_id
        )
        
        publish_progress(job_id, {'step': 'C_progress', 'percent': 90})
        await asyncio.sleep(0.5)

        publish_progress(job_id, {'step': 'C_done', 'status': 'done'})

        # Calculate compressed bandwidth metrics
        compressed_size = Path(output_path).stat().st_size if Path(output_path).exists() else 0

        # --- Complete ---
        publish_progress(job_id, {
            'step': 'complete',
            'status': 'complete',
            'video_url': f'/outputs/{output_filename}',
            'filename': output_filename,
            'original_size': original_size,
            'compressed_size': compressed_size,
            'saving_percent': round((1 - compressed_size / max(1, original_size)) * 100) if original_size > 0 else 0
        })

        await update_log(
            status_str="success",
            duration=video_duration,
            bandwidth=compressed_size,
            video_name=output_filename
        )

    except Exception as e:
        publish_progress(job_id, {
            'step': 'error',
            'status': 'error',
            'message': str(e)
        })
        await update_log(
            status_str="failed",
            error=str(e)
        )
    finally:
        # Dispose task engine connection
        try:
            await task_engine.dispose()
        except Exception:
            pass

        # Cleanup original uploaded video
        if video_path and Path(video_path).exists():
            try:
                Path(video_path).unlink()
            except Exception:
                pass

