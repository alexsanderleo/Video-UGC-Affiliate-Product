# Auto Klip VIP — Clip Studio (Clone Opus Clip)

Fitur baru di video.agomart.com: paste link YouTube → AI memotong jadi klip pendek viral
(30–90 detik) → caption karaoke per-kata presisi → editor timeline lengkap → export MP4.

## Arsitektur

```
static/clipstudio.html + js/clipstudio.js   Halaman utama (input → progress → grid klip)
static/clipeditor.html + js/clipeditor.js   Editor (transkrip, preview, timeline, sidebar)
api/v1/clipstudio.py                        REST API (auth JWT, polling status 2 detik)
core/clipstudio/
  download.py     Tahap A: yt-dlp ≤1080p + WAV 16kHz (whisper) + WAV 8kHz (waveform)
  transcribe.py   Tahap B: faster-whisper word_timestamps + VAD + deteksi filler ID/EN
  curate.py       Tahap C: Claude/Qwen pilih segmen viral + skor + judul (fallback ~45s)
  reframe.py      Tahap D: YuNet face tracking 2fps + smoothing → crop keyframes
  captions.py     Template caption + builder ASS karaoke (preview = hasil burn)
  assets.py       Thumbnail kartu + sprite strip timeline (1 frame/detik)
  export.py       FASE 4: ffmpeg cut/concat + crop sendcmd + overlay + burn ASS + ducking
  runner.py       Orkestrasi pipeline + dispatch (thread lokal / celery VPS)
models/clipstudio.py                        clip_projects, clip_transcripts, clips, clip_exports
storage/projects/{id}/                      source.mp4, audio, clips/{id}/ (mudah diganti S3)
```

## Menjalankan LOKAL (Windows dev)

```powershell
# 1) venv + deps (sudah dibuat: venv-win)
py -3.13 -m venv venv-win
venv-win\Scripts\pip install -r requirements-saas.txt yt-dlp faster-whisper opencv-python anthropic

# 2) .env: DATABASE_URL sqlite + CLIP_USE_CELERY=false (tanpa redis/celery — pakai thread)
# 3) jalankan
venv-win\Scripts\python -m uvicorn main:app --host 0.0.0.0 --port 8077
# buka http://localhost:8077/clipstudio  (login dulu di / )
```

ffmpeg & ffprobe wajib ada di PATH. Model Whisper & YuNet di-download otomatis saat pertama dipakai.

## Deploy VPS (aaPanel + supervisor, pola sama dgn fitur lain)

1. `git pull origin main`, lalu `venv/bin/pip install yt-dlp faster-whisper opencv-python-headless anthropic`
2. `.env` VPS tambahkan:
   ```
   CLIP_USE_CELERY=true
   WHISPER_MODEL=medium        # atau small jika CPU terbatas
   ANTHROPIC_API_KEY=sk-ant-...   # opsional, fallback otomatis ke DASHSCOPE qwen-plus
   PEXELS_API_KEY=...             # opsional, utk B-roll stock footage
   ```
3. Restart KEDUA daemon: uvicorn + celery
   (`/www/server/panel/pyenv/bin/supervisorctl -c /etc/supervisor/supervisord.conf restart all`)
4. Nginx: `client_max_body_size 2G;` untuk upload video panjang.

## Konfigurasi (.env)

| Var | Default | Keterangan |
|---|---|---|
| `CLIP_USE_CELERY` | `false` | `true` di VPS → pipeline lewat worker celery |
| `WHISPER_MODEL` | `small` | model whisper LOKAL (fallback) |
| `GROQ_API_KEY` | — | Transcribe (Whisper API) + curation (Llama) super cepat |
| `ANTHROPIC_API_KEY` | — | AI curation Claude (opsional) |
| `PEXELS_API_KEY` | — | B-roll stock footage (gratis di pexels.com/api) |
| `CLIP_MAX_SOURCE_SECONDS` | `7200` | Tolak video > 2 jam |

## Switch provider AI dari ADMIN PANEL (/mimin → section "Clip Studio AI")

Tanpa redeploy, admin bisa mengatur (disimpan di tabel `app_settings`):
- **Transcribing**: `Groq Whisper API` (default bila ada key — video 3,5 mnt ≈ 8 detik)
  atau `Whisper Lokal` (gratis, fallback otomatis bila Groq gagal/limit/audio >25MB).
- **Analyzing**: `Auto (Groq→Claude→Qwen)` / `Groq Llama` / `Qwen` / `Claude` /
  `Custom` (OpenAI-compatible: base_url + model + key — bisa OpenAI, Mistral, dll).
- **Kriteria prompt kurasi**: textarea custom (placeholder `{bahasa}` otomatis diganti);
  kontrak output JSON dikunci sistem agar parsing tidak rusak.

Benchmark lokal (video TED 3,5 menit, 3 klip): **±18 detik end-to-end**
(download 6s → transcribe Groq 8s → curation Groq ~2s → reframe+assets paralel ~4s).
Sebelumnya whisper lokal CPU: ±5-6 menit.

## Keputusan teknis (deviasi dari spec, sesuai izin poin 1)

- **Next.js → vanilla JS + halaman statis**: app existing 100% FastAPI + static HTML;
  satu stack = deploy aaPanel tanpa node. Editor tetap penuh (canvas timeline, rAF caption).
- **Zustand → store vanilla + undo snapshot** (50 langkah, command pattern di snapshot).
- **wavesurfer.js → canvas custom**: decode WAV 8kHz via WebAudio, integrasi zoom/cut-shading
  lebih presisi dalam satu kanvas dengan sprite & playhead.
- **PostgreSQL → SQLite (dev) / MySQL (VPS)**: mengikuti infra DB existing app; skema portable.
- **MediaPipe → YuNet OpenCV**: MediaPipe belum support Python 3.13; YuNet setara & ringan.

## Fitur paritas Opus (batch 2 — hasil riset detail opus.pro)

- **ClipAnything**: prompt natural di halaman input ("cari momen tentang X") +
  **Reprompting** di halaman hasil (kurasi ulang tanpa download/transkrip ulang).
- **Virality score breakdown**: hook / flow / value / trend per klip (bar di kartu).
- **AI Voice-over**: teks -> suara (edge-tts, 5 voice ID/EN), blok di timeline,
  preview ikut bunyi, dicampur otomatis saat export.
- **AI Speech enhancement**: toggle di menu Audio (highpass + denoise afftdn + loudnorm).
- **Auto censor**: scan kata kasar ID/EN -> audio di-mute + caption tersensor (k****).
- **Export to XML**: timeline FCP7 xmeml utk Adobe Premiere Pro / DaVinci Resolve.
- **Post sosial**: AI caption per platform (TikTok/YT Shorts/IG Reels) + tombol salin
  + share link export. (Auto-post OAuth = TODO, butuh app credentials platform.)
- **Thumbnail generator**: frame pilihan + judul besar -> unduh JPG.
- **Brand intro/outro cards**: gambar/video dipasang di awal/akhir export.
- **Custom font upload** (.ttf/.otf): dipakai di preview (@font-face) & burn (fontsdir).
- **Sumber import luas**: YouTube, Vimeo, Twitch, Facebook, Google Drive, TikTok,
  Instagram, X/Twitter, Loom, Rumble, dll (via yt-dlp).
- **Bulk export**: render semua klip 1 klik dari halaman hasil.

## Fitur editor ala CapCut (batch 3 — 100% BUATAN SENDIRI, NOL API eksternal)

Prinsip proyek jangka panjang: seluruh fitur di bawah diimplementasi mandiri di kode
kita (preview = CSS/canvas, export = filter ffmpeg setara). TIDAK ada panggilan ke
API CapCut/capcut-mate/pihak ketiga — checklist fiturnya saja yang dipakai sebagai acuan:

- Timeline multi-track output-time (cut benar-benar hilang, segmen menyambung,
  trim sambungan dgn drag, pindah layer dgn drag vertikal) ~ create_draft/tracks.
- add_videos/add_images ~ media & B-roll overlay multi-layer + drag/resize di preview.
- add_audios ~ multi-track audio: volume + fade per item, blok di lane audio.
- add_sticker ~ stiker emoji dirender PNG transparan LOKAL via canvas.
- add_captions/add_text_style ~ caption karaoke + keyword highlight + 9 template.
- add_effects ~ 7 efek (BW, vintage, blur, glow, grain, shake, negatif) sbg blok track.
- add_masks ~ mask bentuk (lingkaran / rounded) per overlay (geq alpha di ffmpeg).
- add_keyframes ~ rotasi + posisi/skala per item + crop keyframes wajah; animasi
  properti waktu = animasi masuk/keluar.
- get_text_animations / get_image_animations ~ animasi masuk (fade/naik/turun/geser),
  keluar (fade/turun), loop (pulse) utk teks & gambar — preview & export identik.
- gen_video/gen_video_status ~ export celery/thread + polling progres.

## TODO / stub yang dicatat (tidak dihilangkan diam-diam)

- [ ] Transisi `zoom` & `slide` saat export dirender sebagai fade halus (TODO: chain xfade).
- [ ] Auto-post / social scheduler OAuth (TikTok/YT/IG) — butuh app credentials resmi tiap
      platform; UI & AI caption sudah siap di menu Post.
- [ ] Credits: tampil di header & dihitung (1 menit sumber = 1 credit) — enforcement
      limit per plan belum (semua user bisa proses).
- [ ] AI B-roll *generatif* (Opus Pro punya text-to-video) — saat ini stock Pexels.
- [ ] Team workspace, folder, analytics, API publik — di luar scope v1.
- [ ] `docker-compose.yml` tersedia untuk dev non-Windows; produksi tetap aaPanel supervisor.
- [ ] Auto-cleanup storage project lama (retensi 30 hari) belum dijadwalkan.

## Acceptance criteria (status uji lokal)

- [x] Paste link YouTube → klip 9:16 + caption otomatis tanpa intervensi (teruji end-to-end).
- [x] Karaoke highlight sinkron via `video.currentTime` + word timestamps (rAF, toleransi <100ms).
- [x] Hapus kata/kalimat → preview skip cut_ranges → export ikut terpotong (select/concat).
- [x] Export MP4: caption ASS identik template preview, crop ikut wajah (sendcmd), audio sinkron.
- [x] Refresh editor → edit tersimpan (autosave PUT edit_state + tombol Save changes).
