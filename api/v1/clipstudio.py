"""
API Clip Studio (Auto Klip VIP) — clone Opus Clip.

Alur: POST /clipstudio/projects (URL YouTube / upload) -> polling
GET /clipstudio/projects/{id}/status tiap 2 detik -> GET detail (grid klip)
-> editor pakai GET/PUT /clipstudio/clips/{id} -> POST export -> download.
"""

import json
import re
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db
from core.config import get_settings
from core.clipstudio.ai import extract_json, llm_complete
from core.clipstudio.captions import CAPTION_FONTS, CAPTION_TEMPLATES
from core.clipstudio.runner import dispatch_export, dispatch_project
from models.clipstudio import Clip, ClipExport, ClipProject, ClipTranscript
from models.user import User

router = APIRouter(prefix="/clipstudio", tags=["Clip Studio"])
settings = get_settings()

# Sumber video yang didukung (semua ditangani yt-dlp, seperti Opus: YouTube,
# Google Drive, Vimeo, Zoom, Rumble, Twitch, Facebook, Loom, dll)
SUPPORTED_URL_RE = re.compile(
    r"^(https?://)?(www\.|m\.)?("
    r"youtube\.com/(watch\?|shorts/|live/)|youtu\.be/"
    r"|vimeo\.com/|twitch\.tv/|rumble\.com/|drive\.google\.com/"
    r"|facebook\.com/|fb\.watch/|loom\.com/|dailymotion\.com/"
    r"|streamable\.com/|tiktok\.com/|instagram\.com/(reel|p|tv)/"
    r"|x\.com/|twitter\.com/"
    r")", re.IGNORECASE
)


def _project_brief(p: ClipProject) -> dict:
    return {
        "id": p.id, "title": p.title, "source_type": p.source_type,
        "source_url": p.source_url, "duration": p.duration,
        "status": p.status, "percent": p.percent, "error_message": p.error_message,
        "credits_used": p.credits_used,
        "thumbnail": f"/storage/projects/{p.id}/thumbnail.jpg" if p.status == "done" or p.percent >= 25 else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "options": p.options or {},
    }


def _clip_brief(c: Clip) -> dict:
    return {
        "id": c.id, "project_id": c.project_id, "start": c.start, "end": c.end,
        "duration": round(c.end - c.start, 2), "title": c.title, "score": c.score,
        "score_breakdown": c.score_breakdown or {},
        "reason": c.reason, "hashtags": c.hashtags or [], "aspect_ratio": c.aspect_ratio,
        "layout_mode": c.layout_mode, "tracker_on": c.tracker_on, "status": c.status,
        "thumbnail": c.thumbnail, "sprite": c.sprite,
    }


async def _get_owned_project(project_id: str, user: User, db: AsyncSession) -> ClipProject:
    proj = await db.get(ClipProject, project_id)
    if not proj:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project tidak ditemukan.")
    if proj.user_id != user.id and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bukan project Anda.")
    return proj


async def _get_owned_clip(clip_id: str, user: User, db: AsyncSession) -> Clip:
    clip = await db.get(Clip, clip_id)
    if not clip:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Klip tidak ditemukan.")
    proj = await db.get(ClipProject, clip.project_id)
    if proj.user_id != user.id and not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Bukan klip Anda.")
    return clip


# ---------- konfigurasi & template ----------

@router.get("/templates")
async def get_templates():
    """Template caption + daftar font bawaan & custom (dipakai input page & editor)."""
    from core.clipstudio.paths import STORAGE_DIR
    custom = []
    fdir = STORAGE_DIR / "fonts"
    if fdir.exists():
        for f in sorted(fdir.glob("*.[ot]tf")):
            family = _ttf_family_name(f.read_bytes()) or f.stem
            custom.append({"family": family, "url": f"/storage/fonts/{f.name}"})
    fonts = CAPTION_FONTS + [c["family"] for c in custom if c["family"] not in CAPTION_FONTS]
    return {"templates": CAPTION_TEMPLATES, "fonts": fonts, "custom_fonts": custom}


@router.get("/music")
async def get_music_library(user: User = Depends(get_current_user)):
    """Library musik bebas royalti (folder backsounds/)."""
    tracks = []
    bdir = settings.BASE_DIR / "backsounds"
    if bdir.exists():
        for f in sorted(bdir.glob("*.mp3")):
            tracks.append({
                "name": f.stem.replace(".mp3", "").replace("_", " ").title(),
                "url": f"/backsounds/{f.name}",
            })
    return {"tracks": tracks}


# ---------- projects ----------

@router.post("/projects")
async def create_project(
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Buat project dari URL YouTube. Body: {source_url, options:{...}}."""
    url = (payload.get("source_url") or "").strip()
    if not url or not SUPPORTED_URL_RE.search(url):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Masukkan link video yang valid (YouTube, Vimeo, Twitch, "
                            "Facebook, Google Drive, TikTok, dll).")
    options = payload.get("options") or {}
    pid = str(uuid.uuid4())
    proj = ClipProject(
        id=pid, user_id=user.id, source_url=url, source_type="youtube",
        title="Memuat info video...", options=options, status="queued", percent=0,
    )
    db.add(proj)
    await db.commit()
    dispatch_project(pid)
    return {"project_id": pid, "status": "queued"}


@router.post("/projects/upload")
async def create_project_upload(
    file: UploadFile = File(...),
    options: str = Form("{}"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Buat project dari file video upload user."""
    try:
        opts = json.loads(options or "{}")
    except json.JSONDecodeError:
        opts = {}
    if not (file.filename or "").lower().endswith((".mp4", ".mov", ".mkv", ".webm", ".m4v")):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "Format file harus video (mp4/mov/mkv/webm).")
    pid = str(uuid.uuid4())
    from core.clipstudio.paths import project_dir
    pdir = project_dir(pid)
    dest = pdir / ("upload" + Path(file.filename).suffix.lower())
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    opts["upload_path"] = str(dest)
    opts["upload_name"] = file.filename
    proj = ClipProject(
        id=pid, user_id=user.id, source_url=None, source_type="upload",
        title=file.filename, options=opts, status="queued", percent=0,
    )
    db.add(proj)
    await db.commit()
    dispatch_project(pid)
    return {"project_id": pid, "status": "queued"}


@router.get("/projects")
async def list_projects(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(ClipProject).where(ClipProject.user_id == user.id)
        .order_by(ClipProject.created_at.desc()).limit(50)
    )).scalars().all()
    return {"projects": [_project_brief(p) for p in rows]}


@router.get("/projects/{project_id}/status")
async def project_status(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Polling progress tiap 2 detik (Downloading -> ... -> Done)."""
    proj = await _get_owned_project(project_id, user, db)
    return {
        "status": proj.status, "percent": proj.percent,
        "error_message": proj.error_message, "title": proj.title,
        "duration": proj.duration, "credits_used": proj.credits_used,
    }


@router.get("/projects/{project_id}")
async def project_detail(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    proj = await _get_owned_project(project_id, user, db)
    clips = (await db.execute(
        select(Clip).where(Clip.project_id == project_id).order_by(Clip.score.desc())
    )).scalars().all()
    tr = (await db.execute(
        select(ClipTranscript).where(ClipTranscript.project_id == project_id)
    )).scalar_one_or_none()
    words = (tr.words if tr else []) or []

    def snippet(c: Clip) -> str:
        toks = [w["word"].strip() for w in words if w["start"] >= c.start and w["end"] <= c.end]
        s = " ".join(toks[:30])
        return s + ("..." if len(toks) > 30 else "")

    out = []
    for c in clips:
        d = _clip_brief(c)
        d["snippet"] = snippet(c)
        out.append(d)
    return {"project": _project_brief(proj), "clips": out}


@router.post("/projects/{project_id}/reprompt")
async def reprompt_project(
    project_id: str,
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Reprompting ala Opus (ClipAnything): kurasi ulang klip dengan prompt/opsi baru
    memakai transkrip yang sudah ada — tanpa download & transkripsi ulang.
    Body: {prompt, clip_length?, max_clips?}
    """
    proj = await _get_owned_project(project_id, user, db)
    if proj.status not in ("done", "error"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Project masih diproses — tunggu selesai dulu.")
    new_options = {
        "prompt": (payload.get("prompt") or "").strip()[:500],
        "clip_length": payload.get("clip_length"),
        "max_clips": payload.get("max_clips"),
    }
    proj.status = "analyzing"
    proj.percent = 56
    proj.error_message = None
    await db.commit()
    from core.clipstudio.runner import dispatch_reprompt
    dispatch_reprompt(project_id, new_options)
    return {"project_id": project_id, "status": "analyzing"}


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    proj = await _get_owned_project(project_id, user, db)
    await db.delete(proj)
    await db.commit()
    import shutil
    from core.clipstudio.paths import PROJECTS_DIR
    shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
    return {"ok": True}


# ---------- clips (editor) ----------

@router.get("/clips/{clip_id}")
async def clip_detail(
    clip_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Data lengkap untuk editor: klip + transkrip word-level + info sumber."""
    clip = await _get_owned_clip(clip_id, user, db)
    proj = await db.get(ClipProject, clip.project_id)
    tr = (await db.execute(
        select(ClipTranscript).where(ClipTranscript.project_id == proj.id)
    )).scalar_one_or_none()

    sprite_meta = {}
    if clip.sprite:
        from core.clipstudio.paths import STORAGE_DIR
        meta_file = STORAGE_DIR / clip.sprite[len("/storage/"):].replace("sprite.jpg", "sprite.json")
        if meta_file.exists():
            sprite_meta = json.loads(meta_file.read_text(encoding="utf-8"))

    return {
        "clip": {
            **_clip_brief(clip),
            "crop_keyframes": clip.crop_keyframes or [],
            "caption_style": clip.caption_style or {},
            "edit_state": clip.edit_state or {},
            "sprite_meta": sprite_meta,
        },
        "project": {
            "id": proj.id, "title": proj.title, "duration": proj.duration,
            "fps": proj.fps, "width": proj.width, "height": proj.height,
            "source": f"/storage/projects/{proj.id}/source.mp4",
            "waveform_audio": f"/storage/projects/{proj.id}/audio.8k.wav",
        },
        "words": (tr.words if tr else []) or [],
        "language": tr.language if tr else "id",
    }


@router.put("/clips/{clip_id}")
async def update_clip(
    clip_id: str,
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Autosave editor: edit_state, caption_style, title, aspect/layout/tracker."""
    clip = await _get_owned_clip(clip_id, user, db)
    allowed = {"title", "aspect_ratio", "layout_mode", "tracker_on",
               "caption_style", "edit_state", "crop_keyframes", "start", "end"}
    for k, v in payload.items():
        if k in allowed:
            setattr(clip, k, v)
    await db.commit()
    return {"ok": True, "saved_at": __import__("datetime").datetime.utcnow().isoformat()}


# ---------- export ----------

@router.post("/clips/{clip_id}/export")
async def export_clip(
    clip_id: str,
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    clip = await _get_owned_clip(clip_id, user, db)
    eid = str(uuid.uuid4())
    exp = ClipExport(
        id=eid, clip_id=clip.id,
        resolution=payload.get("resolution", "1080p"),
        watermark=bool(payload.get("watermark", False)),
        status="queued", percent=0,
    )
    db.add(exp)
    await db.commit()
    dispatch_export(eid)
    return {"export_id": eid, "status": "queued"}


@router.get("/exports/{export_id}")
async def export_status(
    export_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    exp = await db.get(ClipExport, export_id)
    if not exp:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Export tidak ditemukan.")
    return {
        "id": exp.id, "status": exp.status, "percent": exp.percent,
        "error_message": exp.error_message, "file_path": exp.file_path,
        "file_size": exp.file_size, "resolution": exp.resolution,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
    }


@router.get("/clips/{clip_id}/exports")
async def export_history(
    clip_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_clip(clip_id, user, db)
    rows = (await db.execute(
        select(ClipExport).where(ClipExport.clip_id == clip_id)
        .order_by(ClipExport.created_at.desc())
    )).scalars().all()
    return {"exports": [{
        "id": e.id, "status": e.status, "percent": e.percent,
        "file_path": e.file_path, "resolution": e.resolution,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    } for e in rows]}


# ---------- media upload user ----------

@router.post("/projects/{project_id}/media")
async def upload_media(
    project_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload gambar/video/audio user untuk overlay (menu Media)."""
    await _get_owned_project(project_id, user, db)
    ext = Path(file.filename or "").suffix.lower()
    kinds = {".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".gif": "image",
             ".mp4": "video", ".webm": "video", ".mov": "video",
             ".mp3": "audio", ".wav": "audio", ".m4a": "audio"}
    if ext not in kinds:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Format file tidak didukung.")
    from core.clipstudio.paths import project_dir, rel_storage
    mdir = project_dir(project_id) / "media"
    mdir.mkdir(exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename)
    dest = mdir / f"{uuid.uuid4().hex[:8]}_{safe}"
    with open(dest, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            f.write(chunk)
    return {"url": rel_storage(dest), "type": kinds[ext], "name": file.filename,
            "note": "File media disimpan 30 hari, otomatis terhapus saat project dihapus."}


@router.get("/projects/{project_id}/media")
async def list_media(
    project_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_owned_project(project_id, user, db)
    from core.clipstudio.paths import project_dir, rel_storage
    mdir = project_dir(project_id) / "media"
    items = []
    if mdir.exists():
        kinds = {".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".gif": "image",
                 ".mp4": "video", ".webm": "video", ".mov": "video",
                 ".mp3": "audio", ".wav": "audio", ".m4a": "audio"}
        for f in sorted(mdir.iterdir()):
            k = kinds.get(f.suffix.lower())
            if k:
                items.append({"url": rel_storage(f), "type": k, "name": f.name})
    return {"media": items}


# ---------- B-roll (Pexels) ----------

@router.get("/broll")
async def search_broll(
    q: str,
    media_type: str = "videos",
    user: User = Depends(get_current_user),
):
    """Cari stock footage/gambar Pexels (gratis). Butuh PEXELS_API_KEY di .env."""
    import os
    key = settings.PEXELS_API_KEY or os.getenv("PEXELS_API_KEY", "")
    if not key:
        return {"items": [], "error": "PEXELS_API_KEY belum diisi di .env — fitur B-roll stock nonaktif. "
                                      "Daftar gratis di https://www.pexels.com/api/"}
    headers = {"Authorization": key}
    async with httpx.AsyncClient(timeout=20) as client:
        if media_type == "photos":
            r = await client.get("https://api.pexels.com/v1/search",
                                 params={"query": q, "per_page": 12}, headers=headers)
            data = r.json()
            items = [{
                "type": "image", "thumb": p["src"]["medium"], "url": p["src"]["large"],
                "by": p.get("photographer", ""),
            } for p in data.get("photos", [])]
        else:
            r = await client.get("https://api.pexels.com/videos/search",
                                 params={"query": q, "per_page": 12}, headers=headers)
            data = r.json()
            items = []
            for v in data.get("videos", []):
                files = sorted(v.get("video_files", []), key=lambda f: f.get("width") or 0)
                pick = next((f for f in files if (f.get("width") or 0) >= 720), files[-1] if files else None)
                if pick:
                    items.append({"type": "video", "thumb": v.get("image"),
                                  "url": pick["link"], "by": v.get("user", {}).get("name", "")})
    return {"items": items}


# ---------- AI B-Roll: generate GAMBAR dari caption terpilih (Gemini cookie sendiri) ----------

@router.post("/clips/{clip_id}/broll-image")
async def generate_broll_image(
    clip_id: str,
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    AI B-Roll ala Opus: caption/teks terpilih -> AI generate gambar -> simpan ke media
    project. Frontend menaruhnya di timeline tepat pada rentang caption tsb.
    Engine: gemini-webapi via cookie akun sendiri (diatur admin di /mimin),
    port dari aplikasi klinik-bot-kecantikan-v3 milik user.
    """
    import asyncio as _asyncio
    clip = await _get_owned_clip(clip_id, user, db)
    text = (payload.get("text") or "").strip()[:600]
    manual_prompt = (payload.get("prompt") or "").strip()[:800]
    if not text and not manual_prompt:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Teks caption / prompt kosong.")

    # Susun prompt visual: manual dipakai apa adanya; dari caption, LLM merangkai
    # deskripsi visual English (lebih akurat utk image model). Gagal LLM -> teks mentah.
    if manual_prompt:
        img_prompt = manual_prompt
    else:
        img_prompt = ""
        try:
            raw = llm_complete(
                "You are a visual prompt writer for an image generator. Reply ONLY the prompt text.",
                f'Caption from a talking video: "{text}"\n\n'
                f"Write ONE short English image-generation prompt (max 40 words) that visually "
                f"illustrates this caption for use as b-roll. Concrete scene, no text overlay, "
                f"no captions, photorealistic.",
                max_tokens=120,
            )
            img_prompt = (raw or "").strip().strip('"')[:800]
        except Exception:
            pass
        if not img_prompt:
            img_prompt = f"Photorealistic b-roll illustration of: {text}"
    img_prompt += " Vertical 9:16 composition, high quality, no text, no watermark."

    from core.clipstudio.imagegen import GenerateError, generate_image
    try:
        png = await _asyncio.to_thread(generate_image, img_prompt)
    except GenerateError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Gagal generate gambar: {e}")

    from core.clipstudio.paths import project_dir, rel_storage
    mdir = project_dir(clip.project_id) / "media"
    mdir.mkdir(exist_ok=True)
    dest = mdir / f"broll_ai_{uuid.uuid4().hex[:8]}.png"
    dest.write_bytes(png)
    return {"url": rel_storage(dest), "prompt": img_prompt, "bytes": len(png)}


# ---------- AI tools (hook, emoji, keyword, b-roll keywords) ----------

@router.post("/clips/{clip_id}/ai")
async def clip_ai_tools(
    clip_id: str,
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    action: hooks | emoji | keywords | broll_keywords
    Mengembalikan saran AI; frontend yang menerapkan ke edit_state (undo-able).
    """
    clip = await _get_owned_clip(clip_id, user, db)
    tr = (await db.execute(
        select(ClipTranscript).where(ClipTranscript.project_id == clip.project_id)
    )).scalar_one_or_none()
    words = (tr.words if tr else []) or []
    lang = tr.language if tr else "id"
    lang_label = "bahasa Indonesia" if (lang or "id").startswith("id") else "English"

    es = clip.edit_state or {}
    cstart = float(es.get("extend", {}).get("start", clip.start))
    cend = float(es.get("extend", {}).get("end", clip.end))
    indexed = [(i, w) for i, w in enumerate(words)
               if w["end"] > cstart and w["start"] < cend]
    text = " ".join(w["word"].strip() for _, w in indexed)[:6000]
    action = payload.get("action")

    if action == "hooks":
        # AI hook/CTA ala Opus: gaya bicara + posisi (awal/akhir) + kata kunci opsional
        style = (payload.get("style") or "serius").strip().lower()
        position = (payload.get("position") or "hook").strip().lower()   # hook | cta
        keywords = (payload.get("keywords") or "").strip()[:200]
        style_map = {
            "serius": "serius dan berwibawa",
            "semangat": "penuh semangat dan energik",
            "lucu": "lucu dan menghibur",
            "santai": "santai seperti ngobrol dengan teman",
            "misterius": "misterius yang bikin penasaran",
        }
        tone = style_map.get(style, style_map["serius"])
        if position == "cta":
            task = (f"Buat 3 alternatif kalimat CTA PENUTUP video (max 12 kata). "
                    f"WAJIB berupa kalimat AJAKAN BERTINDAK kepada penonton — diawali kata kerja seperti "
                    f"'Follow', 'Komen', 'Share', 'Like', 'Simpan' — dikaitkan dgn isi video. "
                    f"Contoh bentuk: 'Follow untuk tips seperti ini tiap hari!'")
        else:
            task = (f"Buat 3 alternatif kalimat HOOK pembuka yang sangat menarik (max 12 kata) "
                    f"untuk diucapkan AI voice-over + text overlay di detik-detik pertama.")
        extra = f"\nWajib menyinggung topik/kata kunci ini: {keywords}" if keywords else ""
        raw = llm_complete(
            "Kamu copywriter video viral. Jawab HANYA JSON murni.",
            f"Transkrip klip ({lang_label}): \"{text}\"\n\n"
            f"{task}\nGaya bahasa: {tone}.{extra}\n"
            f"Tulis dalam {lang_label}, tanpa emoji. Output: [\"kalimat1\", \"kalimat2\", \"kalimat3\"]",
            max_tokens=400,
        )
        hooks = extract_json(raw) or []
        if not isinstance(hooks, list) or not hooks:
            hooks = (["Follow untuk tips berikutnya!", "Komen pendapatmu di bawah!", "Share ke temanmu sekarang!"]
                     if position == "cta" else
                     ["Tonton sampai habis!", "Kamu wajib tahu ini", "Jangan skip bagian ini"])
        return {"hooks": [str(h)[:160] for h in hooks[:3]]}

    if action == "emoji":
        numbered = " ".join(f"[{i}]{w['word'].strip()}" for i, w in indexed)
        raw = llm_complete(
            "Kamu editor caption video. Jawab HANYA JSON murni.",
            f"Kata-kata klip dengan index: {numbered[:6000]}\n\n"
            f"Pilih kata di AKHIR kalimat yang cocok diberi 1 emoji relevan (max 6 kata). "
            f'Output: {{"index_kata": "emoji"}} contoh {{"12": "🔥", "27": "😱"}}',
            max_tokens=400,
        )
        data = extract_json(raw) or {}
        edits = {}
        for k, v in (data.items() if isinstance(data, dict) else []):
            try:
                i = int(k)
            except ValueError:
                continue
            if 0 <= i < len(words):
                edits[str(i)] = words[i]["word"].strip() + " " + str(v)[:4]
        return {"word_edits": edits}

    if action == "keywords":
        numbered = " ".join(f"[{i}]{w['word'].strip()}" for i, w in indexed)
        raw = llm_complete(
            "Kamu editor caption video. Jawab HANYA JSON murni.",
            f"Kata-kata klip dengan index: {numbered[:6000]}\n\n"
            f"Tandai 1-2 kata PENTING per kalimat untuk di-highlight warna. "
            f'Output: {{"index_kata": "#FFD400"}} (boleh #FFD400 kuning atau #FF3CAC pink)',
            max_tokens=500,
        )
        data = extract_json(raw) or {}
        kw = {}
        for k, v in (data.items() if isinstance(data, dict) else []):
            try:
                i = int(k)
            except ValueError:
                continue
            color = str(v) if re.match(r"^#[0-9A-Fa-f]{6}$", str(v)) else "#FFD400"
            if 0 <= i < len(words):
                kw[str(i)] = color
        return {"keyword_colors": kw}

    if action == "censor":
        # Auto censor (tanpa LLM): scan daftar kata kasar ID/EN pada rentang klip
        from core.clipstudio.captions import CENSOR_WORDS
        import re as _re
        found = {}
        for i, w in indexed:
            tok = _re.sub(r"[^\w]", "", w["word"].strip().lower())
            if tok in CENSOR_WORDS:
                found[str(i)] = w["word"].strip()
        return {"censored_words": list(map(int, found.keys())), "preview": found}

    if action == "post_copy":
        # AI title/description/hashtag per platform (fitur "Customize Your Post" Opus)
        raw = llm_complete(
            "Kamu social media manager profesional. Jawab HANYA JSON murni.",
            f"Transkrip klip ({lang_label}): \"{text}\"\n"
            f"Judul klip: \"{clip.title or ''}\"\n\n"
            f"Buat copy posting untuk 3 platform dalam {lang_label}:\n"
            f"- tiktok: caption pendek catchy + 4 hashtag\n"
            f"- youtube: judul Shorts (max 90 char) + deskripsi 2 kalimat + 4 hashtag\n"
            f"- instagram: caption reels engaging + 5 hashtag\n"
            f'Output: {{"tiktok": "...", "youtube": {{"title": "...", "description": "..."}}, '
            f'"instagram": "..."}}',
            max_tokens=800,
        )
        data = extract_json(raw) or {}
        if not data:
            tags = " ".join((clip.hashtags or [])[:4])
            data = {
                "tiktok": f"{clip.title or 'Klip viral'} {tags}",
                "youtube": {"title": (clip.title or "Klip Viral")[:90],
                            "description": f"{clip.reason or ''} {tags}"},
                "instagram": f"{clip.title or 'Klip viral'} ✨ {tags}",
            }
        return {"post_copy": data}

    if action == "broll_keywords":
        raw = llm_complete(
            "Kamu video editor. Jawab HANYA JSON murni.",
            f"Transkrip klip: \"{text}\"\n\n"
            f"Sarankan 5 kata kunci pencarian stock footage B-roll dalam English "
            f"(Pexels) yang relevan dengan isi pembicaraan. Output: [\"kw1\", ...]",
            max_tokens=200,
        )
        kws = extract_json(raw) or []
        if not isinstance(kws, list) or not kws:
            kws = ["business meeting", "city timelapse", "nature landscape"]
        return {"keywords": [str(k)[:50] for k in kws[:6]]}

    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "action tidak dikenal.")


# ---------- AI Voice-over (multi-engine: Edge / Supertonic 3 OFFLINE / Piper OFFLINE / gTTS) ----------

EDGE_VOICES = [
    {"id": "edge:id-ID-ArdiNeural", "name": "Ardi (Pria, Indonesia)"},
    {"id": "edge:id-ID-GadisNeural", "name": "Gadis (Wanita, Indonesia)"},
    {"id": "edge:ms-MY-OsmanNeural", "name": "Osman (Pria, Melayu)"},
    {"id": "edge:ms-MY-YasminNeural", "name": "Yasmin (Wanita, Melayu)"},
    {"id": "edge:en-US-ChristopherNeural", "name": "Christopher (Male, English)"},
    {"id": "edge:en-US-JennyNeural", "name": "Jenny (Female, English)"},
    {"id": "edge:en-US-GuyNeural", "name": "Guy (Male, English)"},
    {"id": "edge:en-US-AnaNeural", "name": "Ana (Child, English)"},
    {"id": "edge:ja-JP-NanamiNeural", "name": "Nanami (Wanita, Jepang)"},
    {"id": "edge:ko-KR-SunHiNeural", "name": "SunHi (Wanita, Korea)"},
]
SUPERTONIC_VOICES = (
    [{"id": f"supertonic:F{i}", "name": f"Supertonic F{i} (Wanita {i}, Indonesia)"} for i in range(1, 6)] +
    [{"id": f"supertonic:M{i}", "name": f"Supertonic M{i} (Pria {i}, Indonesia)"} for i in range(1, 6)]
)


def _engine_available(mod: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


@router.get("/voices")
async def list_voices():
    """Daftar suara TTS per engine — hanya engine yang terpasang di server ini."""
    groups = [{"engine": "edge", "label": "Edge TTS (online, gratis)", "offline": False,
               "voices": EDGE_VOICES}]
    if _engine_available("supertonic"):
        groups.append({"engine": "supertonic", "label": "Supertonic 3 (OFFLINE, natural)",
                       "offline": True, "voices": SUPERTONIC_VOICES})
    if _engine_available("piper"):
        groups.append({"engine": "piper", "label": "Piper (OFFLINE)", "offline": True,
                       "voices": [{"id": "piper:id_news", "name": "Piper News (Indonesia)"}]})
    if _engine_available("gtts"):
        groups.append({"engine": "gtts", "label": "Google Translate TTS (online)", "offline": False,
                       "voices": [{"id": "gtts:id", "name": "Google TTS (Indonesia)"}]})
    # kompatibilitas lama: daftar datar
    flat = [v for g in groups for v in g["voices"]]
    return {"groups": groups, "voices": flat}


@router.post("/clips/{clip_id}/voiceover")
async def generate_voiceover(
    clip_id: str,
    payload: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI Voice-over multi-engine: teks -> audio narasi. Frontend menaruhnya di timeline."""
    clip = await _get_owned_clip(clip_id, user, db)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Teks voice-over kosong.")
    voice = payload.get("voice") or "edge:id-ID-ArdiNeural"
    if ":" not in voice:                      # kompat: id lama tanpa prefix = edge
        voice = "edge:" + voice
    engine, _, vname = voice.partition(":")

    from core.clipstudio.paths import project_dir, rel_storage
    mdir = project_dir(clip.project_id) / "media"
    mdir.mkdir(exist_ok=True)
    dest = mdir / f"vo_{uuid.uuid4().hex[:8]}.mp3"
    text = text[:8000]   # cukup utk dubbing penuh klip 90 detik

    try:
        if engine == "supertonic":
            # Supertonic 3 OFFLINE (sudah punya fallback edge/gtts internal)
            from core.tts_local import generate_supertonic
            await generate_supertonic(text, str(dest), vname)
        elif engine == "piper":
            from core.tts_local import generate_piper
            await generate_piper(text, str(dest), "ffmpeg")
        elif engine == "gtts":
            from core.tts_local import generate_gtts
            await generate_gtts(text, str(dest))
        else:
            import edge_tts
            com = edge_tts.Communicate(text, vname)
            await com.save(str(dest))
        if not dest.exists() or dest.stat().st_size < 500:
            raise RuntimeError(f"Engine {engine} tidak menghasilkan audio.")
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Gagal generate voice-over ({engine}): {e}")

    # durasi via ffprobe
    import subprocess as sp
    dur = 0.0
    try:
        out = sp.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", str(dest)], capture_output=True, text=True).stdout
        dur = float(out.strip() or 0)
    except Exception:
        pass
    return {"url": rel_storage(dest), "duration": round(dur, 2), "voice": voice,
            "text": text[:120]}


# ---------- Export to XML (Premiere / DaVinci) ----------

@router.get("/clips/{clip_id}/xml")
async def export_xml(
    clip_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download timeline FCP7 XML — bisa di-import ke Premiere Pro / DaVinci Resolve."""
    from fastapi.responses import Response
    clip = await _get_owned_clip(clip_id, user, db)
    proj = await db.get(ClipProject, clip.project_id)
    from core.clipstudio.xml_export import build_xmeml
    xml = build_xmeml(proj, clip, clip.edit_state or {})
    safe = re.sub(r"[^A-Za-z0-9 _-]", "", (clip.title or "klip"))[:60].strip() or "klip"
    return Response(
        content=xml, media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{safe}.xml"'},
    )


# ---------- Custom fonts (Brand) ----------

def _ttf_family_name(data: bytes) -> Optional[str]:
    """Parser minimal tabel 'name' TTF/OTF — ambil family name (nameID 1)."""
    import struct
    try:
        num_tables = struct.unpack(">H", data[4:6])[0]
        name_off = None
        for i in range(num_tables):
            rec = data[12 + i * 16: 12 + i * 16 + 16]
            tag = rec[0:4]
            if tag == b"name":
                name_off = struct.unpack(">I", rec[8:12])[0]
                break
        if name_off is None:
            return None
        count, str_off = struct.unpack(">HH", data[name_off + 2: name_off + 6])
        storage = name_off + str_off
        best = None
        for i in range(count):
            r = data[name_off + 6 + i * 12: name_off + 6 + i * 12 + 12]
            plat, enc, lang, nid, length, off = struct.unpack(">HHHHHH", r)
            if nid != 1:
                continue
            raw = data[storage + off: storage + off + length]
            if plat == 3:  # Windows, UTF-16BE
                best = raw.decode("utf-16-be", "ignore")
            elif best is None:
                best = raw.decode("latin-1", "ignore")
        return (best or "").strip() or None
    except Exception:
        return None


@router.post("/fonts")
async def upload_font(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Upload font custom (.ttf/.otf) — dipakai caption preview & burn export."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".ttf", ".otf"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Hanya file .ttf / .otf.")
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Font maksimal 8MB.")
    family = _ttf_family_name(data) or Path(file.filename).stem
    from core.clipstudio.paths import STORAGE_DIR
    fdir = STORAGE_DIR / "fonts"
    fdir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename)
    dest = fdir / safe
    dest.write_bytes(data)
    return {"family": family, "url": f"/storage/fonts/{safe}", "name": file.filename}


@router.get("/fonts")
async def list_fonts(user: User = Depends(get_current_user)):
    """Font custom yang sudah diupload (family + url utk @font-face frontend)."""
    from core.clipstudio.paths import STORAGE_DIR
    fdir = STORAGE_DIR / "fonts"
    out = []
    if fdir.exists():
        for f in sorted(fdir.glob("*.[ot]tf")):
            family = _ttf_family_name(f.read_bytes()) or f.stem
            out.append({"family": family, "url": f"/storage/fonts/{f.name}"})
    return {"fonts": out}
