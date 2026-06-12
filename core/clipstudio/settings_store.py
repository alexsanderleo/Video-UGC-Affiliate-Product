"""
Pengaturan Clip Studio yang bisa di-switch dari ADMIN PANEL (tanpa redeploy).

Disimpan di tabel app_settings (key-value, sama dengan fitur rotasi AI lama).
Dibaca worker secara sync (pipeline berjalan di thread/celery).

Kunci:
  clip_transcribe_provider  local | groq                  (default: groq bila ada key)
  clip_transcribe_model     whisper-large-v3-turbo | whisper-large-v3
  clip_groq_api_key         API key Groq (fallback env GROQ_API_KEY)
  clip_curate_provider      auto | groq | qwen | anthropic | custom
  clip_curate_model         override model (kosong = default provider)
  clip_curate_base_url      khusus provider custom (OpenAI-compatible)
  clip_curate_api_key       khusus provider custom
  clip_curate_criteria      kriteria pemilihan klip custom (kosong = bawaan)
"""

import os

from dotenv import load_dotenv

from core.clipstudio.db import sync_session

load_dotenv()  # pastikan GROQ_API_KEY dkk dari .env terbaca di proses mana pun (server/worker/skrip)

CLIP_SETTING_KEYS = [
    "clip_transcribe_provider", "clip_transcribe_model", "clip_groq_api_key",
    "clip_curate_provider", "clip_curate_model", "clip_curate_base_url",
    "clip_curate_api_key", "clip_curate_criteria",
]

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_LLM = "llama-3.3-70b-versatile"
GROQ_DEFAULT_WHISPER = "whisper-large-v3-turbo"
QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

DEFAULT_CRITERIA = (
    "- Punya HOOK kuat di 3 detik pertama.\n"
    "- Satu ide utuh — JANGAN memotong di tengah kalimat; gunakan batas waktu kalimat di atas.\n"
    "- Beri skor viralitas total 0-100 beserta alasan singkat (hook kuat / topik tren / punchline).\n"
    "- Breakdown skor 0-100 per komponen: hook (daya tarik 3 dtk pertama), flow (kelancaran alur), "
    "value (nilai/insight bagi penonton), trend (relevansi topik tren).\n"
    "- Judul clickbait dalam {bahasa}.\n"
    "- 3 hashtag relevan."
)


def get_clip_settings() -> dict:
    """Baca seluruh setting Clip Studio dari DB (sync) + default cerdas."""
    from models.ai_provider import AppSetting
    vals = {}
    try:
        with sync_session() as s:
            for k in CLIP_SETTING_KEYS:
                row = s.get(AppSetting, k)
                vals[k] = (row.value or "").strip() if row and row.value else ""
    except Exception:
        vals = {k: "" for k in CLIP_SETTING_KEYS}

    groq_key = vals.get("clip_groq_api_key") or os.getenv("GROQ_API_KEY", "")
    vals["clip_groq_api_key"] = groq_key
    if not vals.get("clip_transcribe_provider"):
        vals["clip_transcribe_provider"] = "groq" if groq_key else "local"
    if not vals.get("clip_transcribe_model"):
        vals["clip_transcribe_model"] = GROQ_DEFAULT_WHISPER
    if not vals.get("clip_curate_provider"):
        vals["clip_curate_provider"] = "auto"
    return vals
