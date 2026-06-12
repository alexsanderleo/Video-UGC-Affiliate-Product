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


def llm_complete(system: str, user: str, max_tokens: int = 4000) -> str | None:
    """Satu completion teks. Return None bila semua provider gagal/tidak terkonfigurasi."""
    # 1) Anthropic Claude
    api_key = settings.ANTHROPIC_API_KEY or os.getenv("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        except Exception as e:
            logger.warning("[ClipStudio AI] Anthropic gagal: %s — coba fallback Qwen", e)

    # 2) Qwen teks (DashScope, OpenAI-compatible)
    dash_key = os.getenv("DASHSCOPE_API_KEY", "")
    if dash_key:
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=dash_key,
                base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            )
            resp = client.chat.completions.create(
                model="qwen-plus",
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return resp.choices[0].message.content
        except Exception as e:
            logger.warning("[ClipStudio AI] Qwen fallback gagal: %s", e)

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
