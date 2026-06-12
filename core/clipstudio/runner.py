"""
Orchestrator pipeline Clip Studio: A Download -> B Transcribe -> C Curate (AI)
-> D Reframe -> E Assets -> Done. Progress ditulis ke DB (polling 2 detik dari frontend).

Dispatch:
- Lokal/dev  (CLIP_USE_CELERY=false): thread background + semaphore (1 job berjalan,
  sisanya antri 'queued') — tidak butuh Redis di Windows.
- VPS        (CLIP_USE_CELERY=true) : Celery task (lihat core/tasks.py).
Satu klip gagal -> klip lain tetap lanjut (Tahap F).
"""

import logging
import threading
import traceback
import uuid

from core.config import get_settings
from core.clipstudio.db import sync_session, update_project_progress, update_export_progress

logger = logging.getLogger(__name__)
settings = get_settings()

_local_slot = threading.Semaphore(1)   # 1 pipeline berat pada satu waktu (CPU-bound)
_export_slot = threading.Semaphore(1)


def process_project(project_id: str):
    """Pipeline lengkap satu project. Aman dipanggil dari thread maupun celery."""
    from models.clipstudio import Clip, ClipProject, ClipTranscript
    from core.clipstudio.download import (ClipSourceError, download_youtube, prepare_upload)
    from core.clipstudio.transcribe import transcribe_words
    from core.clipstudio.curate import curate_clips
    from core.clipstudio.reframe import compute_crop_keyframes
    from core.clipstudio.assets import generate_clip_assets
    from core.clipstudio.paths import project_dir

    with _local_slot:
        try:
            with sync_session() as s:
                proj = s.get(ClipProject, project_id)
                if not proj:
                    return
                options = dict(proj.options or {})
                source_type = proj.source_type
                source_url = proj.source_url

            # --- A. Download (0-25%) ---
            update_project_progress(project_id, status="downloading", percent=1)
            if source_type == "youtube":
                meta = download_youtube(
                    project_id, source_url,
                    progress_cb=lambda p: update_project_progress(
                        project_id, percent=1 + int(p * 0.22)),
                )
            else:
                upload_path = options.get("upload_path")
                meta = prepare_upload(project_id, upload_path, options.get("upload_name", ""))

            credits = max(1, int(round(meta["duration"] / 60)))
            update_project_progress(
                project_id, percent=25,
                title=meta.get("title"), duration=meta["duration"], fps=meta["fps"],
                width=meta["width"], height=meta["height"], credits_used=credits,
            )

            # Rentang proses opsional (menit X..Y)
            rng_start = float(options.get("range_start") or 0)
            rng_end = float(options.get("range_end") or 0) or meta["duration"]
            rng_start = max(0.0, min(rng_start, meta["duration"]))
            rng_end = max(rng_start + 1, min(rng_end, meta["duration"]))

            # --- B. Transcribe (25-55%) ---
            # Provider dipilih dari admin panel: groq (API, super cepat) / local (whisper PC).
            update_project_progress(project_id, status="transcribing", percent=26)
            pdir = project_dir(project_id)
            lang_opt = options.get("language")
            lang = None if lang_opt in (None, "", "auto") else lang_opt
            audio = str(pdir / "audio.16k.wav")
            prog = lambda p: update_project_progress(project_id, percent=26 + int(p * 0.29))

            from core.clipstudio.settings_store import get_clip_settings
            cfg = get_clip_settings()
            tr = None
            if cfg["clip_transcribe_provider"] == "groq" and cfg["clip_groq_api_key"]:
                try:
                    from core.clipstudio.transcribe import transcribe_words_groq
                    tr = transcribe_words_groq(
                        audio, language=lang,
                        api_key=cfg["clip_groq_api_key"],
                        model=cfg["clip_transcribe_model"],
                        progress_cb=prog,
                    )
                except Exception as e:
                    logger.warning("[ClipStudio] Groq transcribe gagal (%s) — fallback whisper lokal", e)
            if tr is None:
                tr = transcribe_words(audio, language=lang, progress_cb=prog)
            words = [w for w in tr["words"] if w["start"] >= rng_start and w["end"] <= rng_end] \
                if (rng_start > 0 or rng_end < meta["duration"]) else tr["words"]
            if not words:
                raise ClipSourceError(
                    "Tidak ada ucapan terdeteksi pada video / rentang yang dipilih. "
                    "Pastikan video berisi suara bicara."
                )
            with sync_session() as s:
                s.add(ClipTranscript(project_id=project_id, language=tr["language"], words=words))
            update_project_progress(project_id, percent=55)

            # --- C. AI Curation (55-65%) ---
            update_project_progress(project_id, status="analyzing", percent=56)
            segs = curate_clips(
                words, tr["language"],
                clip_length=options.get("clip_length", "auto"),
                max_clips=int(options.get("max_clips") or 10),
                prompt=options.get("prompt", ""),   # ClipAnything
            )
            if not segs:
                raise ClipSourceError("AI tidak menemukan segmen klip yang layak pada video ini.")
            update_project_progress(project_id, percent=65)

            _create_clips_and_assets(
                project_id, segs, options,
                src_w=meta["width"], src_h=meta["height"],
            )
            update_project_progress(project_id, status="done", percent=100)

        except ClipSourceError as e:
            update_project_progress(project_id, status="error", error=str(e))
        except Exception as e:
            logger.error("[ClipStudio] pipeline error %s\n%s", e, traceback.format_exc())
            update_project_progress(
                project_id, status="error",
                error=f"Terjadi kesalahan saat memproses: {e}",
            )


def _create_clips_and_assets(project_id: str, segs: list, options: dict,
                             src_w: int, src_h: int):
    """Buat baris Clip + reframe wajah + assets (dipakai proses awal & reprompt)."""
    from models.clipstudio import Clip
    from core.clipstudio.reframe import compute_crop_keyframes
    from core.clipstudio.assets import generate_clip_assets
    from core.clipstudio.paths import project_dir

    aspect = options.get("aspect_ratio", "9:16")
    template = options.get("caption_template", "opus-green")
    clip_ids = []
    with sync_session() as s:
        for seg in segs:
            cid = str(uuid.uuid4())
            clip_ids.append(cid)
            s.add(Clip(
                id=cid, project_id=project_id,
                start=seg["start"], end=seg["end"], title=seg["title"],
                score=seg["score"], score_breakdown=seg.get("breakdown"),
                reason=seg["reason"], hashtags=seg["hashtags"],
                aspect_ratio=aspect, layout_mode="fill", tracker_on=True,
                caption_style={"template": template},
                edit_state={"cut_ranges": [], "deleted_words": [], "word_edits": {}},
                status="processing",
            ))

    # --- D. Reframe + E. Assets per klip (65-99%) — PARALEL antar klip ---
    from concurrent.futures import ThreadPoolExecutor, as_completed

    update_project_progress(project_id, status="reframing", percent=66)
    source = str(project_dir(project_id) / "source.mp4")
    n = len(clip_ids)

    def _process_one(i: int):
        cid, seg = clip_ids[i], segs[i]
        try:
            kfs = compute_crop_keyframes(source, seg["start"], seg["end"], src_w, src_h)
            with sync_session() as s:
                c = s.get(Clip, cid)
                c.crop_keyframes = kfs
        except Exception as e:
            logger.warning("[ClipStudio] reframe klip %s gagal: %s", cid[:8], e)
        try:
            assets = generate_clip_assets(project_id, cid, source, seg["start"], seg["end"])
            with sync_session() as s:
                c = s.get(Clip, cid)
                c.thumbnail = assets["thumbnail"]
                c.sprite = assets["sprite"]
                c.status = "ready"
        except Exception as e:
            logger.warning("[ClipStudio] assets klip %s gagal: %s", cid[:8], e)
            with sync_session() as s:
                c = s.get(Clip, cid)
                c.status = "ready"  # tetap bisa diedit walau thumbnail gagal

    done = 0
    with ThreadPoolExecutor(max_workers=min(4, max(1, n))) as pool:
        futures = [pool.submit(_process_one, i) for i in range(n)]
        for _ in as_completed(futures):
            done += 1
            if done == max(1, n // 2):
                update_project_progress(project_id, status="rendering")
            update_project_progress(project_id, percent=66 + int(done / n * 33))


def reprocess_project(project_id: str, new_options: dict):
    """
    Reprompting ala Opus: kurasi ulang dengan prompt/opsi baru TANPA download &
    transkripsi ulang. Klip lama dihapus, diganti hasil baru.
    """
    from models.clipstudio import Clip, ClipProject, ClipTranscript
    from core.clipstudio.curate import curate_clips
    from core.clipstudio.download import ClipSourceError

    with _local_slot:
        try:
            with sync_session() as s:
                proj = s.get(ClipProject, project_id)
                if not proj:
                    return
                tr = s.query(ClipTranscript).filter_by(project_id=project_id).first()
                if not tr or not tr.words:
                    raise ClipSourceError("Transkrip tidak ditemukan — proses ulang dari awal.")
                words = tr.words
                language = tr.language
                options = dict(proj.options or {})
                options.update({k: v for k, v in (new_options or {}).items() if v is not None})
                proj.options = options
                src_w, src_h = proj.width, proj.height

            update_project_progress(project_id, status="analyzing", percent=56, error="")
            segs = curate_clips(
                words, language,
                clip_length=options.get("clip_length", "auto"),
                max_clips=int(options.get("max_clips") or 10),
                prompt=options.get("prompt", ""),
            )
            if not segs:
                raise ClipSourceError(
                    "AI tidak menemukan momen yang cocok dengan prompt itu. Coba prompt lain."
                )
            # ganti klip lama dengan hasil baru
            with sync_session() as s:
                for old in s.query(Clip).filter_by(project_id=project_id).all():
                    s.delete(old)
            update_project_progress(project_id, percent=65)
            _create_clips_and_assets(project_id, segs, options, src_w, src_h)
            update_project_progress(project_id, status="done", percent=100)
        except ClipSourceError as e:
            update_project_progress(project_id, status="error", error=str(e))
        except Exception as e:
            logger.error("[ClipStudio] reprompt error %s\n%s", e, traceback.format_exc())
            update_project_progress(project_id, status="error", error=f"Reprompt gagal: {e}")


def process_export(export_id: str):
    """Render final satu export (FASE 4)."""
    from models.clipstudio import Clip, ClipExport, ClipProject, ClipTranscript
    from core.clipstudio.export import render_clip_export
    from core.clipstudio.paths import rel_storage

    with _export_slot:
        try:
            with sync_session() as s:
                exp = s.get(ClipExport, export_id)
                if not exp:
                    return
                clip = s.get(Clip, exp.clip_id)
                proj = s.get(ClipProject, clip.project_id)
                tr = s.query(ClipTranscript).filter_by(project_id=proj.id).first()
                words = (tr.words if tr else []) or []

            update_export_progress(export_id, status="rendering", percent=1)
            out = render_clip_export(
                proj, clip, exp, words,
                progress_cb=lambda p: update_export_progress(export_id, percent=p),
            )
            update_export_progress(
                export_id, status="done", percent=100,
                file_path=rel_storage(out), file_size=out.stat().st_size,
            )
        except Exception as e:
            logger.error("[ClipStudio] export error %s\n%s", e, traceback.format_exc())
            update_export_progress(export_id, status="error", error=str(e))


# ---------- dispatch ----------

def dispatch_project(project_id: str):
    if settings.CLIP_USE_CELERY:
        from core.celery_app import celery_app
        celery_app.send_task("core.tasks.clipstudio_process_project", args=[project_id])
    else:
        threading.Thread(target=process_project, args=(project_id,), daemon=True).start()


def dispatch_export(export_id: str):
    if settings.CLIP_USE_CELERY:
        from core.celery_app import celery_app
        celery_app.send_task("core.tasks.clipstudio_process_export", args=[export_id])
    else:
        threading.Thread(target=process_export, args=(export_id,), daemon=True).start()


def dispatch_reprompt(project_id: str, new_options: dict):
    if settings.CLIP_USE_CELERY:
        from core.celery_app import celery_app
        celery_app.send_task("core.tasks.clipstudio_reprompt", args=[project_id, new_options])
    else:
        threading.Thread(target=reprocess_project, args=(project_id, new_options), daemon=True).start()
