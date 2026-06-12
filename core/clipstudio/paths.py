"""
Layout storage Clip Studio — folder lokal ./storage/, mudah diganti S3 nanti.

storage/
  projects/{project_id}/
    source.mp4            video sumber (hasil yt-dlp / upload)
    audio.16k.wav         mono 16kHz untuk Whisper
    audio.8k.wav          mono 8kHz kecil untuk waveform timeline (meniru audio.resize.wav Opus)
    thumbnail.jpg         thumbnail video sumber
    meta.json             metadata yt-dlp
    clips/{clip_id}/
      thumb.jpg           thumbnail kartu klip
      sprite.jpg          strip frame tiap ~1 detik utk timeline
      sprite.json         metadata sprite (cols, tile_w, tile_h, interval)
      export_{export_id}.mp4
"""

from pathlib import Path

from core.config import get_settings

settings = get_settings()

STORAGE_DIR: Path = settings.STORAGE_DIR
PROJECTS_DIR: Path = STORAGE_DIR / "projects"


def project_dir(project_id: str) -> Path:
    d = PROJECTS_DIR / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def clip_dir(project_id: str, clip_id: str) -> Path:
    d = project_dir(project_id) / "clips" / clip_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def rel_storage(p: Path) -> str:
    """Path absolut -> path URL relatif '/storage/...' (selalu pakai forward slash)."""
    return "/storage/" + p.resolve().relative_to(STORAGE_DIR.resolve()).as_posix()
