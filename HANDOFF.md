# HANDOFF — Status Pekerjaan (per 2026-06-02)

Catatan serah-terima untuk melanjutkan di PC lain. Kode sudah di GitHub (`main`).
Di PC baru: `git pull origin main`, lalu baca file ini.

## Ringkasan sesi terakhir

### 1. Login customer via agomart (sudah BERES & teruji 200 OK)
- App `video.agomart.com` memverifikasi login customer ke API pusat
  `agomart.com` (`POST /api/auth/app-login`, body `{email,password,tool:"video"}`).
- Commit terkait: `9a21a1e` (fitur), `11dcf48` (hardening), `6887bbb` (fix bug).
- **Bug 500 yang sudah diperbaiki:** `datetime.utcnow()` (naive) dibandingkan
  dengan `user.expired_at` (tz-aware) → `TypeError`. Diperbaiki dengan helper
  `is_expired()` di [core/security.py](core/security.py), dipakai di
  [api/v1/auth.py](api/v1/auth.py) & [api/deps.py](api/deps.py).
- Config agomart ada di [core/config.py](core/config.py): `AGOMART_API_URL`,
  `AGOMART_TOOL_SLUG="video"`, `AGOMART_AUTH_ENABLED=True`.

### 2. Panel admin SWITCH MODEL AI (commit `f095d8d`) — SELESAI DIKODE, BELUM DI-DEPLOY/TES
Tujuan: ganti provider/model analisis video dari panel admin tanpa redeploy.
Mode tunggal (pilih 1 aktif) + toggle rotasi opsional. Isi awal: **Qwen saja**
(arsitektur extensible untuk Gemini/Groq menyusul).

File:
- Baru: [models/ai_provider.py](models/ai_provider.py) (tabel `ai_providers` + `app_settings`),
  [core/ai_provider.py](core/ai_provider.py) (`get_active_ai_config()` baca via engine sync pymysql + fallback `.env`).
- Ubah: [core/pipeline.py](core/pipeline.py) (`step_a_video_understanding` dinamis),
  [api/v1/admin.py](api/v1/admin.py) (endpoint HTMX `/admin/ai-providers/*`),
  [static/admin.html](static/admin.html) (section "Model AI"),
  [core/database.py](core/database.py) (seed Qwen Plus + rotasi off),
  [models/__init__.py](models/__init__.py).

Fakta penting yang sudah diverifikasi:
- Qwen DashScope = OpenAI-compatible + terima video langsung (drop-in).
- Gemini OpenAI-compat **TIDAK** terima video (hanya gambar) → butuh adapter
  "frames" atau native SDK saat ditambah nanti. Adapter selain `openai_video`
  saat ini di-stub `NotImplementedError`.

## YANG MASIH HARUS DILAKUKAN (lanjutan)
1. **Tes fitur switch model di VPS instance debug port 8011** (produksi tidak disentuh):
   ```bash
   cd /www/wwwroot/video.agomart.com
   git pull origin main
   venv/bin/uvicorn main:app --host 127.0.0.1 --port 8011 > /tmp/dbg.log 2>&1 &
   sleep 7
   cat /tmp/dbg.log | grep -iE "STARTUP|tables ready|SEED|error|traceback"
   fuser -k 8011/tcp
   ```
   Harapan log: `[DB] tables ready`, `[SEED] AI provider defaults ready.`, tanpa traceback.
2. Tes via browser: login admin → `/mimin` → section "Model AI" muncul, "Qwen Plus"
   aktif → generate 1 video sukses (log: `[Step A] Provider aktif: Qwen Plus`).
3. Kalau bersih → **restart produksi** (uvicorn + celery WAJIB keduanya):
   `/www/server/panel/pyenv/bin/supervisorctl -c /etc/supervisor/supervisord.conf restart all`
4. Opsional lanjutan: tambah dukungan Gemini (frame-sampling via FFmpeg → `image_url`)
   dan/atau Groq Llama Vision; isi adapter `openai_frames`.

## Catatan operasional VPS
- Deploy = managed **supervisor aaPanel**. `supervisorctl` full path:
  `/www/server/panel/pyenv/bin/supervisorctl -c /etc/supervisor/supervisord.conf`.
- venv VPS: `/www/wwwroot/video.agomart.com/venv/` (yang `.venv` macOS di repo diabaikan).
- App di port 8000 (nginx proxy), celery worker terpisah. Restart wajib agar kode baru aktif.
- `.env` produksi ada di VPS (tidak di git) — jangan commit `.env`.

## Cara melanjutkan chat dengan AI di PC baru
Riwayat chat ini lokal (tidak ikut git). Di PC baru, mulai sesi Claude Code baru
di folder repo, lalu minta: "baca HANDOFF.md, lanjutkan dari poin YANG MASIH HARUS
DILAKUKAN".
