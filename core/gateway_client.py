"""
Klien Gateway Generator "Flow" (Gemini) — https://flowapi.agomart.com

Tiga kemampuan:
  • TEKS  (Gemini, SINKRON)      -> gemini_text(...)              langsung balas
  • GAMBAR (Nano Banana, ASYNC)  -> generate_image(...)           submit -> poll
  • VIDEO (Veo, ASYNC)           -> generate_video(...)           submit -> poll

Aturan:
  - Panggil HANYA dari backend (X-API-Key rahasia, jangan ke JS browser).
  - URL hasil (image_url/video_url) AUTO-HAPUS ~7 hari -> WAJIB unduh & simpan
    (pakai *_to_file / download()).
  - Konfigurasi via .env: GATEWAY_FLOW_URL, GATEWAY_FLOW_KEY, GATEWAY_FLOW_DOMAIN
    (lihat core/config.py). 'domain' otomatis disertakan utk kuota & log per-domain.

Kontrak lengkap: docs/INTEGRASI-DOMAIN-LUAR.md (repo agomart-gemini-gateway).
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Callable, Optional, Union

import httpx

from core.config import get_settings

_s = get_settings()

BASE_URL = (getattr(_s, "GATEWAY_FLOW_URL", "") or "https://flowapi.agomart.com").rstrip("/")
API_KEY = getattr(_s, "GATEWAY_FLOW_KEY", "") or ""
DOMAIN = getattr(_s, "GATEWAY_FLOW_DOMAIN", "") or "revideo.agomart.com"

ImageInput = Union[str, Path, "tuple[bytes, str]"]


class GatewayError(RuntimeError):
    """Gagal memanggil gateway: HTTP error, job 'failed', atau timeout."""


def _headers() -> dict:
    return {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout)


# --------------------------------------------------------------------------- #
# STATUS / kesehatan server                                                    #
# --------------------------------------------------------------------------- #
def is_healthy(timeout: float = 10) -> bool:
    """True bila gateway hidup (GET /healthz, tanpa auth)."""
    try:
        with _client(timeout) as c:
            return c.get(f"{BASE_URL}/healthz").json().get("ok") is True
    except Exception:
        return False


def status(timeout: float = 10) -> dict:
    """Status server real-time (kapasitas + antrian, dianonimkan)."""
    with _client(timeout) as c:
        r = c.get(f"{BASE_URL}/v1/status", headers=_headers())
        r.raise_for_status()
        return r.json()


def available(kind: str, timeout: float = 10) -> bool:
    """kind = 'text' | 'image' | 'video'. True bila minimal 1 server siap."""
    try:
        return bool(status(timeout).get(kind, {}).get("available"))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# TEKS — Gemini (SINKRON, tanpa poll)                                           #
# --------------------------------------------------------------------------- #
def gemini_text(
    prompt: str,
    system: str = "",
    json_mode: bool = False,
    new_chat: bool = True,
    timeout: int = 120,
) -> Union[str, dict]:
    """
    Generate teks via Gemini app (langsung balas, ~5-40 dtk).

    Return:
      - dict  -> bila json_mode=True dan JSON valid (field 'parsed').
      - str   -> selainnya (field 'text', selalu ada).
    """
    if not prompt:
        raise ValueError("prompt wajib diisi")
    body = {
        "prompt": prompt,
        "system": system,
        "json_mode": json_mode,
        "new_chat": new_chat,
        "timeout": timeout,
    }
    with _client(timeout + 30) as c:
        r = c.post(f"{BASE_URL}/v1/gemini/text", headers=_headers(), json=body)
        if r.status_code != 200:
            raise GatewayError(f"gemini/text HTTP {r.status_code}: {r.text[:200]}")
        d = r.json()
    if json_mode and d.get("parsed") is not None:
        return d["parsed"]
    return d.get("text", "")


# --------------------------------------------------------------------------- #
# GAMBAR & VIDEO — async (submit -> poll)                                       #
# --------------------------------------------------------------------------- #
def _encode_images(images: Optional[list]) -> Optional[list]:
    """images = list path/str ATAU tuple (bytes, mime) -> format gateway."""
    if not images:
        return None
    out = []
    for item in images:
        if isinstance(item, (str, Path)):
            p = Path(item)
            mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            data = base64.b64encode(p.read_bytes()).decode()
        elif isinstance(item, tuple) and len(item) == 2:
            raw, mime = item
            data = base64.b64encode(raw).decode()
        else:
            continue
        out.append({"mime": mime, "data": data})
    return out or None


def _submit(path: str, body: dict, timeout: float = 30) -> str:
    with _client(timeout) as c:
        r = c.post(f"{BASE_URL}{path}", headers=_headers(), json=body)
    if r.status_code != 200:
        raise GatewayError(f"submit {path} HTTP {r.status_code}: {r.text[:200]}")
    jid = r.json().get("job_id")
    if not jid:
        raise GatewayError(f"submit {path}: tidak ada job_id")
    return jid


def _poll(
    job_id: str,
    url_field: str,
    interval: float,
    timeout: float,
    on_progress: Optional[Callable[[dict], None]] = None,
) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _client(20) as c:
            d = c.get(f"{BASE_URL}/v1/jobs/{job_id}", headers=_headers()).json()
        if on_progress:
            try:
                on_progress(d)
            except Exception:
                pass
        st = d.get("status")
        if st == "done":
            return d.get(url_field) or ""
        if st == "failed":
            raise GatewayError(d.get("error") or "job gagal (failed)")
        time.sleep(interval)
    raise GatewayError(f"timeout menunggu job {job_id}")


def generate_image(
    prompt: str,
    model: str = "nano_pro",          # "nano_pro" | "nano_2"
    aspect_ratio: str = "9:16",        # "1:1" | "16:9" | "9:16" | "3:4"
    images: Optional[list] = None,     # gambar REFERENSI (maks 10)
    timeout: float = 300,
    interval: float = 2,
    on_progress: Optional[Callable[[dict], None]] = None,
) -> str:
    """Generate gambar (async). Return image_url. Unduh dgn download()."""
    body = {"prompt": prompt, "model": model, "aspect_ratio": aspect_ratio, "domain": DOMAIN}
    enc = _encode_images(images)
    if enc:
        body["images"] = enc
    jid = _submit("/v1/generate-image-flow", body)
    return _poll(jid, "image_url", interval, timeout, on_progress)


def generate_video(
    prompt: str,
    model: str = "omni",               # "omni"(10s)|"veo31_lite"(8s)|"veo31_lite_lp"|"veo31_fast_fl"
    aspect_ratio: str = "9:16",        # "16:9" | "9:16"
    images: Optional[list] = None,     # maks 3; veo31_fast_fl WAJIB 2 (awal,akhir)
    timeout: float = 1800,
    interval: float = 5,
    on_progress: Optional[Callable[[dict], None]] = None,
) -> str:
    """Generate video (async). Return video_url. Unduh dgn download()."""
    body = {"prompt": prompt, "model": model, "aspect_ratio": aspect_ratio, "domain": DOMAIN}
    enc = _encode_images(images)
    if enc:
        body["images"] = enc
    jid = _submit("/v1/generate-video", body)
    return _poll(jid, "video_url", interval, timeout, on_progress)


# --------------------------------------------------------------------------- #
# Unduh & simpan (URL gateway auto-hapus -> simpan sendiri)                     #
# --------------------------------------------------------------------------- #
def download(url: str, dest_path: Union[str, Path], timeout: float = 180) -> Path:
    """Unduh image_url/video_url ke file. Terima URL absolut atau path relatif gateway."""
    full = url if url.startswith("http") else f"{BASE_URL}{url}"
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _client(timeout) as c:
        r = c.get(full)
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest


def generate_image_to_file(prompt: str, dest_path: Union[str, Path], **kw) -> Path:
    """Generate gambar lalu langsung simpan ke dest_path. Return Path."""
    return download(generate_image(prompt, **kw), dest_path)


def generate_video_to_file(prompt: str, dest_path: Union[str, Path], **kw) -> Path:
    """Generate video lalu langsung simpan ke dest_path. Return Path."""
    return download(generate_video(prompt, **kw), dest_path)
