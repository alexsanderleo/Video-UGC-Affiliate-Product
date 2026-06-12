"""
AI B-Roll: generate GAMBAR via COOKIE akun Gemini sendiri (library `gemini-webapi`).

Port dari gemini_engine.py milik repo klinik-bot-kecantikan-v3 (aplikasi user sendiri),
disesuaikan utk Clip Studio:
- Daftar "server" (1 server = 1 akun Gemini, cookie __Secure-1PSID + __Secure-1PSIDTS)
  disimpan di file `storage/gemini_servers.json` (ter-gitignore, aman) — diimport
  dari panel admin /mimin. Format JSON SAMA dgn output cookies-grabber repo klinik,
  jadi file output/servers.json bisa langsung ditempel.
  (Tidak pakai tabel app_settings karena kolom value VARCHAR(255) — terlalu pendek.)
- Failover + auto-cooldown antar akun seperti aslinya.

CATATAN penting (jujur soal umur panjang): gemini-webapi TIDAK resmi & cookie bisa
basi — fitur ini opsional; kalau gagal, stock B-roll & overlay lokal tetap jalan.
"""
import asyncio
import json
import random
import threading
import time
import uuid
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

GEMINI_TIMEOUT = 120           # panggilan PERTAMA (cold start) bisa >60 dtk; normal ±25-40 dtk
OVERALL_TIMEOUT = 300          # batas total 1 permintaan (termasuk failover antar akun)
COOLDOWN_SECONDS = 300         # akun gagal diistirahatkan 5 menit, sisanya tetap dipakai

_cooldown: dict = {}           # {server_id: epoch_until} — per proses
_clients: dict = {}            # pool client per server (init mahal)


def _servers_file() -> Path:
    from core.clipstudio.paths import STORAGE_DIR
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return STORAGE_DIR / "gemini_servers.json"


def _config_file() -> Path:
    from core.clipstudio.paths import STORAGE_DIR
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return STORAGE_DIR / "gemini_config.json"


DEFAULT_CONFIG = {"mode": "random", "interval_min": 10}
# mode: "random" = acak + failover (bawaan)
#       "rotate" = rotasi terjadwal — tiap `interval_min` menit ganti akun "piket"
#                  (akun lain tetap jadi cadangan failover bila piket gagal)


def load_config() -> dict:
    p = _config_file()
    cfg = dict(DEFAULT_CONFIG)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
        except Exception:
            pass
    cfg["mode"] = cfg.get("mode") if cfg.get("mode") in ("random", "rotate") else "random"
    try:
        cfg["interval_min"] = max(1, min(1440, int(cfg.get("interval_min") or 10)))
    except Exception:
        cfg["interval_min"] = 10
    return cfg


def save_config(mode: str, interval_min: int) -> dict:
    cfg = {
        "mode": mode if mode in ("random", "rotate") else "random",
        "interval_min": max(1, min(1440, int(interval_min or 10))),
    }
    p = _config_file()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return cfg


def rotation_status() -> dict:
    """Info utk panel admin: akun piket sekarang + sisa menit sebelum ganti."""
    cfg = load_config()
    try:
        servers = [s for s in _read_raw() if s.get("enabled", True) and s.get("secure_1psid")]
    except Exception:
        servers = []
    if cfg["mode"] != "rotate" or not servers:
        return {"mode": cfg["mode"], "interval_min": cfg["interval_min"],
                "active": None, "next_in_s": 0, "total": len(servers)}
    window = cfg["interval_min"] * 60
    slot = int(time.time() // window)
    idx = slot % len(servers)
    next_in = window - int(time.time() % window)
    return {"mode": "rotate", "interval_min": cfg["interval_min"],
            "active": servers[idx].get("name") or servers[idx].get("id"),
            "active_id": servers[idx].get("id"),
            "next_in_s": next_in, "total": len(servers)}


class GenerateError(Exception):
    """Error generate yang pesannya aman ditampilkan ke pengguna."""


# ---- event loop latar (gemini-webapi async; worker/endpoint kita sync via thread) ----
_loop = None
_loop_lock = threading.Lock()


def _get_loop():
    global _loop
    if _loop is None:
        with _loop_lock:
            if _loop is None:
                _loop = asyncio.new_event_loop()
                threading.Thread(target=_loop.run_forever, daemon=True).start()
    return _loop


def _run(coro):
    fut = asyncio.run_coroutine_threadsafe(coro, _get_loop())
    try:
        return fut.result(timeout=OVERALL_TIMEOUT)
    except FuturesTimeout:
        fut.cancel()
        raise GenerateError(f"Generate kelamaan (> {OVERALL_TIMEOUT} dtk) dan dihentikan. Coba lagi.")


# ---- daftar server: disimpan di app_settings (key clip_gemini_servers) ----

def _read_raw() -> list:
    p = _servers_file()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_raw(servers: list) -> None:
    p = _servers_file()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(servers, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def load_servers() -> list:
    servers = [s for s in _read_raw() if s.get("enabled", True) and s.get("secure_1psid")]
    if not servers:
        raise GenerateError(
            "Belum ada akun Gemini aktif. Admin: buka /mimin → bagian "
            "'AI B-Roll (Gemini)' → import cookie dari cookies-grabber."
        )
    return servers


# ---- client pool + generate ----

async def _client_for(sv: dict):
    sid = sv.get("id") or ""
    cli = _clients.get(sid)
    if cli is not None:
        return cli
    try:
        from gemini_webapi import GeminiClient
    except ImportError as e:
        raise GenerateError(
            "Library 'gemini-webapi' belum terpasang di server. Jalankan: pip install gemini-webapi==2.0.0"
        ) from e
    psid = sv.get("secure_1psid", "") or ""
    psidts = sv.get("secure_1psidts", "") or ""
    if not psid or psid.upper().startswith("DUMMY") or psid.startswith("TEMPEL"):
        raise GenerateError("Cookie belum diisi (masih placeholder). Ambil cookie via cookies-grabber.")
    cli = GeminiClient(psid, psidts)
    await cli.init(timeout=GEMINI_TIMEOUT, auto_close=False, auto_refresh=True)
    _clients[sid] = cli
    return cli


async def _gen_one(sv: dict, prompt: str) -> bytes:
    import os
    import tempfile
    cli = await _client_for(sv)
    resp = await asyncio.wait_for(cli.generate_content(prompt), timeout=GEMINI_TIMEOUT)
    imgs = getattr(resp, "images", None) or []
    if not imgs:
        raise GenerateError("Gemini membalas teks, bukan gambar. Coba lagi atau ubah prompt.")
    out_dir = tempfile.mkdtemp(prefix="clip_broll_")
    fname = uuid.uuid4().hex + ".png"
    try:
        try:
            await asyncio.wait_for(
                imgs[0].save(path=out_dir, filename=fname, verbose=False, full_size=True),
                timeout=GEMINI_TIMEOUT,
            )
        except TypeError:   # versi library beda signature
            await asyncio.wait_for(
                imgs[0].save(path=out_dir, filename=fname, verbose=False), timeout=GEMINI_TIMEOUT
            )
        full = Path(out_dir) / fname
        if not full.exists():
            files = list(Path(out_dir).glob("*"))
            if not files:
                raise GenerateError("Gambar gagal diunduh dari Gemini.")
            full = files[0]
        return full.read_bytes()
    finally:
        try:
            for f in Path(out_dir).glob("*"):
                f.unlink()
            os.rmdir(out_dir)
        except OSError:
            pass


async def _generate(prompt: str) -> bytes:
    full_prompt = (
        "Generate an image. Output the image directly, do not reply with text.\n\n" + prompt
    )
    servers = load_servers()
    now = time.time()
    ready = [s for s in servers if _cooldown.get(s.get("id"), 0) <= now]
    pool = ready if ready else servers   # semua cooldown → tetap coba semua
    cfg = load_config()
    if cfg["mode"] == "rotate":
        # rotasi terjadwal: tiap interval_min menit ganti akun "piket" (urut daftar);
        # akun piket dicoba duluan, sisanya tetap cadangan failover berurutan
        window = cfg["interval_min"] * 60
        idx = (int(now // window)) % len(servers)
        order = servers[idx:] + servers[:idx]
        in_pool = {s.get("id") for s in pool}
        pool = [s for s in order if s.get("id") in in_pool] or pool
    else:
        random.shuffle(pool)             # acak: bagi beban + failover sederhana
    last = None
    for sv in pool:
        sid = sv.get("id") or ""
        try:
            out = await _gen_one(sv, full_prompt)
            _cooldown.pop(sid, None)
            return out
        except Exception as e:  # noqa: BLE001 — istirahatkan akun ini, coba berikutnya
            last = e
            _clients.pop(sid, None)
            if COOLDOWN_SECONDS > 0:
                _cooldown[sid] = time.time() + COOLDOWN_SECONDS
            continue
    detail = str(last) or repr(last)   # TimeoutError dkk punya str kosong → pakai repr
    raise GenerateError(f"Semua akun Gemini gagal. Detail terakhir: {detail}")


def generate_image(prompt: str) -> bytes:
    """SINKRON (panggil via thread dari endpoint async). prompt → bytes PNG."""
    return _run(_generate(prompt))


# ======================================================================
# Kelola akun cookie (dipakai panel admin /mimin)
# ======================================================================
_ALLOWED = ("id", "name", "gmail", "secure_1psid", "secure_1psidts", "enabled", "weight")


def _clean(d: dict) -> dict:
    return {k: d[k] for k in _ALLOWED if k in d and d[k] is not None}


def list_servers_masked() -> list:
    now = time.time()
    out = []
    for s in _read_raw():
        psid = s.get("secure_1psid", "") or ""
        real = bool(psid) and not psid.upper().startswith("DUMMY") and not psid.startswith("TEMPEL")
        sid = s.get("id", "")
        cd_left = int(_cooldown.get(sid, 0) - now)
        out.append({
            "id": sid,
            "name": s.get("name", ""),
            "gmail": s.get("gmail", ""),
            "enabled": s.get("enabled", True),
            "has_psid": real,
            "has_psidts": bool(s.get("secure_1psidts", "")),
            "psid_preview": (psid[:6] + "…" + psid[-4:]) if len(psid) > 12 else (psid or "(kosong)"),
            "on_cooldown": cd_left > 0,
            "cooldown_left": max(0, cd_left),
        })
    return out


def import_servers(text: str) -> int:
    """Gabung akun dari teks JSON (output cookies-grabber). Return jumlah diproses."""
    text = (text or "").strip()
    if not text:
        raise GenerateError("Tidak ada data JSON.")
    try:
        data = json.loads(text)
    except Exception as e:
        raise GenerateError(f"JSON tidak valid: {e}")
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise GenerateError("Format harus array server (atau satu objek server).")
    cur = _read_raw()
    by_id = {s.get("id"): s for s in cur}
    n = 0
    for raw in data:
        if not isinstance(raw, dict):
            continue
        c = _clean(raw)
        sid = (c.get("id") or "").strip()
        if not sid or not c.get("secure_1psid"):
            continue
        c["id"] = sid
        c.setdefault("name", sid.upper())
        c.setdefault("enabled", True)
        c.setdefault("weight", 1)
        if sid in by_id:
            by_id[sid].update(c)
        else:
            cur.append(c)
            by_id[sid] = c
        _clients.pop(sid, None)
        n += 1
    if not n:
        raise GenerateError("Tidak ada akun valid (butuh 'id' + 'secure_1psid').")
    _write_raw(cur)
    return n


def add_server(sid: str, psid: str, psidts: str = "", name: str = "", gmail: str = "") -> None:
    sid = (sid or "").strip()
    psid = (psid or "").strip()
    if not sid or not psid:
        raise GenerateError("ID server & __Secure-1PSID wajib diisi.")
    cur = _read_raw()
    by_id = {s.get("id"): s for s in cur}
    entry = {
        "id": sid, "name": (name or "").strip() or sid.upper(), "gmail": (gmail or "").strip(),
        "secure_1psid": psid, "secure_1psidts": (psidts or "").strip(),
        "enabled": True, "weight": 1,
    }
    if sid in by_id:
        by_id[sid].update(entry)
    else:
        cur.append(entry)
    _clients.pop(sid, None)
    _write_raw(cur)


def set_enabled(sid: str, enabled: bool) -> None:
    sid = (sid or "").strip()
    cur = _read_raw()
    for s in cur:
        if s.get("id") == sid:
            s["enabled"] = bool(enabled)
            break
    else:
        raise GenerateError(f"Server '{sid}' tidak ditemukan.")
    if not enabled:
        _cooldown.pop(sid, None)
    _clients.pop(sid, None)
    _write_raw(cur)


def delete_server(sid: str) -> None:
    sid = (sid or "").strip()
    cur = [s for s in _read_raw() if s.get("id") != sid]
    _clients.pop(sid, None)
    _write_raw(cur)
