"""
Aset per-klip untuk editor: thumbnail kartu + sprite strip timeline.

Sprite = frame tiap ~1 detik dirangkai jadi 1 gambar (tile horizontal, baris max 10 kolom),
dipakai strip thumbnail timeline di frontend (meniru Opus). Metadata di sprite.json.
"""

import json
import math
import subprocess

from core.clipstudio.paths import clip_dir

TILE_W = 80          # lebar tiap tile sprite (px)
SPRITE_COLS = 10
SPRITE_INTERVAL = 1.0  # 1 frame per detik


def generate_clip_assets(project_id: str, clip_id: str, source_path: str,
                         start: float, end: float) -> dict:
    """Generate thumb.jpg + sprite.jpg + sprite.json. Return path relatif storage."""
    cdir = clip_dir(project_id, clip_id)
    duration = max(0.5, end - start)

    # Thumbnail kartu (ambil 15% masuk ke klip agar tidak kena frame hitam/transisi)
    thumb = cdir / "thumb.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start + duration * 0.15), "-i", str(source_path),
         "-frames:v", "1", "-vf", "scale=360:-2", str(thumb)],
        capture_output=True, text=True,
    )

    # Sprite strip: 1 fps, tile grid
    n_frames = max(1, int(math.ceil(duration / SPRITE_INTERVAL)))
    cols = min(SPRITE_COLS, n_frames)
    rows = int(math.ceil(n_frames / cols))
    sprite = cdir / "sprite.jpg"
    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(start), "-t", str(duration), "-i", str(source_path),
         "-vf", f"fps=1/{SPRITE_INTERVAL},scale={TILE_W}:-2,tile={cols}x{rows}",
         "-frames:v", "1", "-q:v", "5", str(sprite)],
        capture_output=True, text=True,
    )

    sprite_meta = {}
    if sprite.exists() and r.returncode == 0:
        # Tinggi tile dari probe sprite / rows
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "json", str(sprite)],
            capture_output=True, text=True,
        )
        try:
            s = json.loads(probe.stdout)["streams"][0]
            tile_h = int(s["height"] / rows)
        except Exception:
            tile_h = int(TILE_W * 9 / 16)
        sprite_meta = {
            "cols": cols, "rows": rows, "count": n_frames,
            "tile_w": TILE_W, "tile_h": tile_h, "interval": SPRITE_INTERVAL,
        }
        (cdir / "sprite.json").write_text(json.dumps(sprite_meta), encoding="utf-8")

    from core.clipstudio.paths import rel_storage
    return {
        "thumbnail": rel_storage(thumb) if thumb.exists() else None,
        "sprite": rel_storage(sprite) if sprite.exists() else None,
        "sprite_meta": sprite_meta,
    }
