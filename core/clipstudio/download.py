"""
Tahap A — Download video sumber (YouTube via yt-dlp / file upload user).

Output di storage/projects/{id}/:
- source.mp4 (max 1080p, merge bestvideo+bestaudio)
- audio.16k.wav (mono 16kHz — input Whisper)
- audio.8k.wav  (mono 8kHz kecil — waveform timeline frontend, meniru audio.resize.wav Opus)
- thumbnail.jpg + meta.json
"""

import json
import subprocess
from pathlib import Path

from core.config import get_settings
from core.clipstudio.paths import project_dir

settings = get_settings()


class ClipSourceError(Exception):
    """Error sumber video dengan pesan ramah untuk user."""


def _probe(path: str) -> dict:
    """ffprobe -> {duration, fps, width, height}."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,avg_frame_rate:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    stream = (data.get("streams") or [{}])[0]
    num, _, den = (stream.get("avg_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    return {
        "duration": float(data.get("format", {}).get("duration", 0) or 0),
        "fps": round(fps, 3) or 30.0,
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
    }


def _extract_audio(source: Path, pdir: Path):
    """Ekstrak WAV 16kHz (whisper) dan 8kHz (waveform) sekaligus."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), "-vn",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(pdir / "audio.16k.wav"),
         "-ac", "1", "-ar", "8000", "-c:a", "pcm_u8", str(pdir / "audio.8k.wav")],
        capture_output=True, text=True, check=True,
    )


def _extract_thumbnail(source: Path, pdir: Path, at_sec: float = 1.0):
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(at_sec), "-i", str(source),
         "-frames:v", "1", "-vf", "scale=480:-2", str(pdir / "thumbnail.jpg")],
        capture_output=True, text=True,
    )


def download_youtube(project_id: str, url: str, progress_cb=None) -> dict:
    """Download YouTube -> source.mp4 + audio + metadata. Return metadata dict."""
    import yt_dlp

    pdir = project_dir(project_id)
    out_tmpl = str(pdir / "source.%(ext)s")

    def hook(d):
        if progress_cb and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                progress_cb(int(done * 100 / total))

    ydl_opts = {
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "outtmpl": out_tmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "retries": 3,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            dur = float(info.get("duration") or 0)
            if dur and dur > settings.CLIP_MAX_SOURCE_SECONDS:
                raise ClipSourceError(
                    f"Durasi video {int(dur // 60)} menit melebihi batas 2 jam. "
                    "Silakan pilih video yang lebih pendek."
                )
            ydl.download([url])
    except ClipSourceError:
        raise
    except yt_dlp.utils.DownloadError as e:
        msg = str(e).lower()
        if "private" in msg:
            raise ClipSourceError("Video ini PRIVATE — tidak bisa diunduh. Gunakan video publik.")
        if "age" in msg and ("restrict" in msg or "confirm" in msg):
            raise ClipSourceError("Video ini dibatasi umur (age-restricted) dan tidak bisa diproses.")
        if "unavailable" in msg or "removed" in msg:
            raise ClipSourceError("Video tidak tersedia / sudah dihapus. Periksa kembali link-nya.")
        raise ClipSourceError(f"Gagal mengunduh video: {e}")

    source = pdir / "source.mp4"
    if not source.exists():
        # yt-dlp kadang menyimpan dengan ekstensi lain lalu merge — cari hasilnya
        cands = list(pdir.glob("source.*"))
        cands = [c for c in cands if c.suffix.lower() in (".mp4", ".mkv", ".webm")]
        if not cands:
            raise ClipSourceError("File hasil download tidak ditemukan.")
        # Re-mux ke mp4 agar seragam
        subprocess.run(["ffmpeg", "-y", "-i", str(cands[0]), "-c", "copy", str(source)],
                       capture_output=True, text=True)
        if not source.exists():
            subprocess.run(["ffmpeg", "-y", "-i", str(cands[0]), str(source)],
                           capture_output=True, text=True, check=True)
        cands[0].unlink(missing_ok=True)

    meta = _probe(str(source))
    if meta["duration"] > settings.CLIP_MAX_SOURCE_SECONDS:
        raise ClipSourceError("Durasi video melebihi batas 2 jam.")

    meta["title"] = info.get("title") or "Video Tanpa Judul"
    _extract_audio(source, pdir)
    _extract_thumbnail(source, pdir)
    (pdir / "meta.json").write_text(json.dumps({
        "title": meta["title"], "duration": meta["duration"], "fps": meta["fps"],
        "width": meta["width"], "height": meta["height"], "url": url,
        "uploader": info.get("uploader"), "view_count": info.get("view_count"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def prepare_upload(project_id: str, uploaded_path: str, original_name: str = "") -> dict:
    """Video upload user -> normalisasi jadi source.mp4 + audio + metadata."""
    pdir = project_dir(project_id)
    source = pdir / "source.mp4"
    up = Path(uploaded_path)

    try:
        meta = _probe(str(up))
    except subprocess.CalledProcessError:
        raise ClipSourceError(
            "File video tidak valid atau corrupt. Pastikan video bisa diputar normal lalu upload ulang."
        )
    if meta["duration"] > settings.CLIP_MAX_SOURCE_SECONDS:
        raise ClipSourceError("Durasi video melebihi batas 2 jam.")

    if up.suffix.lower() == ".mp4":
        if up.resolve() != source.resolve():
            up.replace(source)
    else:
        # Transcode container lain ke mp4 (copy stream dulu, fallback re-encode)
        r = subprocess.run(["ffmpeg", "-y", "-i", str(up), "-c", "copy", str(source)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not source.exists():
            subprocess.run(["ffmpeg", "-y", "-i", str(up), str(source)],
                           capture_output=True, text=True, check=True)
        up.unlink(missing_ok=True)

    meta["title"] = original_name or "Video Upload"
    _extract_audio(source, pdir)
    _extract_thumbnail(source, pdir)
    (pdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta
