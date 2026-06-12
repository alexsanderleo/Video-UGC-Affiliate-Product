"""
Klien LLM Clip Studio.

Prioritas: Anthropic Claude (ANTHROPIC_API_KEY di .env) -> fallback Qwen teks
via DashScope OpenAI-compatible (DASHSCOPE_API_KEY — sudah dipakai fitur lain
di app ini) -> None (pemanggil wajib punya fallback heuristik).
"""

import json
import logging
import os
import re

from core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _openai_compat(base_url: str, api_key: str, model: str,
                   system: str, user: str, max_tokens: int) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)
    resp = client.chat.completions.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
    )
    return resp.choices[0].message.content


def _anthropic_call(api_key: str, model: str, system: str, user: str, max_tokens: int) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def llm_complete(system: str, user: str, max_tokens: int = 4000) -> str | None:
    """
    Satu completion teks. Provider dipilih dari ADMIN PANEL (app_settings):
    groq | qwen | anthropic | custom | auto (groq -> anthropic -> qwen).
    Return None bila semua gagal/tidak terkonfigurasi.
    """
    from core.clipstudio.settings_store import (
        GROQ_BASE_URL, GROQ_DEFAULT_LLM, QWEN_BASE_URL, get_clip_settings,
    )
    cfg = get_clip_settings()
    provider = cfg.get("clip_curate_provider", "auto")
    model_override = cfg.get("clip_curate_model", "")
    groq_key = cfg.get("clip_groq_api_key", "")
    anth_key = settings.ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY", "")
    dash_key = os.getenv("DASHSCOPE_API_KEY", "")

    # model_override hanya berlaku utk provider TERPILIH; fallback pakai default masing-masing
    def try_groq(use_override):
        if not groq_key:
            return None
        model = (model_override if use_override and model_override else GROQ_DEFAULT_LLM)
        return _openai_compat(GROQ_BASE_URL, groq_key, model, system, user, max_tokens)

    def try_anthropic(use_override):
        if not anth_key:
            return None
        model = (model_override if use_override and model_override else settings.ANTHROPIC_MODEL)
        return _anthropic_call(anth_key, model, system, user, max_tokens)

    def try_qwen(use_override):
        if not dash_key:
            return None
        model = (model_override if use_override and model_override else "qwen-plus")
        return _openai_compat(QWEN_BASE_URL, dash_key, model, system, user, max_tokens)

    def try_custom(use_override):
        base = cfg.get("clip_curate_base_url", "")
        key = cfg.get("clip_curate_api_key", "")
        if not base or not key or not model_override:
            return None
        return _openai_compat(base, key, model_override, system, user, max_tokens)

    chains = {
        "groq": [("groq", try_groq), ("anthropic", try_anthropic), ("qwen", try_qwen)],
        "anthropic": [("anthropic", try_anthropic), ("groq", try_groq), ("qwen", try_qwen)],
        "qwen": [("qwen", try_qwen), ("groq", try_groq), ("anthropic", try_anthropic)],
        "custom": [("custom", try_custom), ("groq", try_groq), ("qwen", try_qwen)],
        "auto": [("groq", try_groq), ("anthropic", try_anthropic), ("qwen", try_qwen)],
    }
    chain = chains.get(provider, chains["auto"])
    for pos, (name, fn) in enumerate(chain):
        try:
            out = fn(pos == 0)
            if out:
                return out
        except Exception as e:
            logger.warning("[ClipStudio AI] provider %s gagal: %s — coba berikutnya", name, e)
    return None


def extract_json(text: str):
    """Ambil JSON murni dari respons LLM (toleran terhadap ```json fences / teks pengantar)."""
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Coba langsung
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Cari array/objek JSON pertama yang seimbang
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == opener:
                depth += 1
            elif text[i] == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None
