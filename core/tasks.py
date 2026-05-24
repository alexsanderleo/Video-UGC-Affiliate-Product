"""
Celery tasks module.
Handles video generation pipeline offloaded to background workers.
"""

import sys
from pathlib import Path

# Programmatically append project root to sys.path for server path resolution
BASE_DIR = Path(__file__).parent.parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

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
    clean_script_for_tts, ensure_backsound
)
from models.generation_log import GenerationLog

settings = get_settings()

# Sync Redis client for worker
r_client = redis.from_url(settings.REDIS_URL)


def publish_progress(job_id: str, data: dict):
    """Publish progress event to Redis Pub/Sub and save cache state."""
    channel = f"task_progress:{job_id}"
    state_key = f"task_state:{job_id}"
    # Cache the latest state for 1 hour to prevent race conditions
    r_client.setex(state_key, 3600, json.dumps(data, ensure_ascii=False))
    # Publish to real-time subscribers
    r_client.publish(channel, json.dumps(data, ensure_ascii=False))


@shared_task(name="core.tasks.render_video")
def render_video_task(
    job_id: str,
    video_path: str,
    voice: str,
    watermark_mode: str,
    watermark_text: str,
    watermark_position: str,
    logo_path: str | None,
    user_id: int,
):
    """Celery task to run the video generation pipeline in a background worker."""
    return asyncio.run(
        async_render_video(
            job_id, video_path, voice, watermark_mode,
            watermark_text, watermark_position, logo_path, user_id
        )
    )


async def async_render_video(
    job_id: str,
    video_path: str,
    voice: str,
    watermark_mode: str,
    watermark_text: str,
    watermark_position: str,
    logo_path: str | None,
    user_id: int,
):
    """Async pipeline implementation called inside the Celery worker."""
    tts_path = None
    srt_path = None
    output_path = None
    start_time = datetime.now(timezone.utc)

    async def update_log(status_str: str, duration: float = 0.0, bandwidth: int = 0, error: str = None, video_name: str = None):
        """Helper to update logging database record."""
        async with async_session() as session:
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
                "Hai semuanya! Kamu harus lihat produk keren ini! "
                "Lihat betapa luar biasanya kualitas produk ini, benar-benar amazing! "
                "Wow, coba perhatikan bagian ini — luar biasa kan?! "
                "Tidak heran produk ini sudah viral di mana-mana! "
                "Buruan grab sebelum kehabisan, link ada di bio ya! "
                f"\n\n[⚠️ Catatan: Script fallback digunakan karena Qwen API error: {str(e)[:100]}]"
            )

        publish_progress(job_id, {
            'step': 'A_done',
            'status': 'done',
            'script_preview': script[:150] + '...' if len(script) > 150 else script
        })

        # --- Step B: Edge-TTS ---
        publish_progress(job_id, {'step': 'B_start', 'status': 'processing'})

        # Clean script
        tts_text = clean_script_for_tts(script)
        if '[' in tts_text and 'Catatan' in tts_text:
            tts_text = re.sub(r'\[.*?Catatan.*?\]', '', tts_text).strip()
        if not tts_text:
            tts_text = "Hai semuanya! Produk ini luar biasa, buruan cek sekarang!"

        tts_filename = f"{job_id}_narasi.mp3"
        tts_path = settings.TEMP_DIR / tts_filename

        # Call Edge-TTS asynchronously
        await step_b_tts(tts_text, voice, str(tts_path))
        publish_progress(job_id, {'step': 'B_done', 'status': 'done'})

        # --- Subtitles (Audio-Driven Sync) ---
        audio_duration = await asyncio.to_thread(get_audio_duration, str(tts_path))
        srt_filename = f"{job_id}_subtitles.srt"
        srt_path = settings.TEMP_DIR / srt_filename

        # Generate subtitle SRT file
        await asyncio.to_thread(generate_srt, tts_text, audio_duration, str(srt_path))

        # --- Step C: FFmpeg blend with anti-copyright ---
        publish_progress(job_id, {'step': 'C_start', 'status': 'processing'})

        backsound = await asyncio.to_thread(ensure_backsound)
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
            subtitle_path=str(srt_path)
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
            'caption': script
        })

        await update_log(
            status_str="success",
            duration=audio_duration,
            bandwidth=bandwidth_bytes,
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
