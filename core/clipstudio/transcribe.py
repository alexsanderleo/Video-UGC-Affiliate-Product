"""
Tahap B — Transkripsi word-level dengan faster-whisper (KUNCI SINKRONISASI CAPTION).

Output: list kata [{word, start, end, conf, is_filler}] — disimpan ke clip_transcripts.words.
Gap antar kata TIDAK disimpan (frontend menghitung gap = start_berikut - end_ini).
"""

import re

from core.config import get_settings

settings = get_settings()

# Filler words bahasa Indonesia & Inggris (dibanding setelah dibersihkan dari tanda baca)
FILLER_WORDS = {
    # Indonesia
    "eee", "ee", "eeh", "emm", "em", "emmm", "hmm", "hmmm", "anu", "apa namanya",
    "kayak", "kayaknya", "gitu", "ya kan", "yakan", "nah",
    # English
    "um", "uh", "umm", "uhh", "like", "you know", "i mean", "actually", "basically",
}

_model = None


def _get_model():
    """Lazy-load model whisper sekali per proses worker."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(
            settings.WHISPER_MODEL,
            device=settings.WHISPER_DEVICE,
            compute_type=settings.WHISPER_COMPUTE,
        )
    return _model


def _clean(word: str) -> str:
    return re.sub(r"[^\w\s]", "", word.strip().lower())


def transcribe_words(audio_path: str, language: str = None, progress_cb=None) -> dict:
    """
    Transkripsi -> {"language": str, "words": [{word,start,end,conf,is_filler}]}.
    language None = auto-detect (dukung id & en).
    """
    model = _get_model()
    segments, info = model.transcribe(
        audio_path,
        language=language if language in ("id", "en") else None,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=5,
    )

    total_dur = float(info.duration or 0) or 1.0
    words = []
    for seg in segments:
        if progress_cb:
            progress_cb(min(99, int(seg.end * 100 / total_dur)))
        for w in (seg.words or []):
            token = (w.word or "").strip()
            if not token:
                continue
            words.append({
                "word": token,
                "start": round(float(w.start), 3),
                "end": round(float(w.end), 3),
                "conf": round(float(w.probability or 0), 3),
                "is_filler": _clean(token) in FILLER_WORDS,
            })

    # Pastikan monotonic (whisper kadang overlap antar segmen)
    for i in range(1, len(words)):
        if words[i]["start"] < words[i - 1]["end"]:
            words[i]["start"] = words[i - 1]["end"]
        if words[i]["end"] < words[i]["start"]:
            words[i]["end"] = words[i]["start"] + 0.05

    return {"language": info.language or (language or "id"), "words": words}


def transcribe_words_groq(audio_path: str, language: str = None,
                          api_key: str = "", model: str = "whisper-large-v3-turbo",
                          progress_cb=None) -> dict:
    """
    Transkripsi via Groq Whisper API (jauh lebih cepat dari CPU lokal).
    Audio dikompres dulu ke mp3 mono 32kbps agar muat limit upload (~25MB).
    Raise exception bila gagal -> pemanggil fallback ke whisper lokal.
    """
    import subprocess
    import httpx
    from pathlib import Path

    if not api_key:
        raise RuntimeError("GROQ_API_KEY kosong")

    src = Path(audio_path)
    mp3 = src.with_suffix(".groq.mp3")
    if progress_cb:
        progress_cb(5)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000",
         "-b:a", "32k", str(mp3)],
        capture_output=True, text=True, check=True,
    )
    size_mb = mp3.stat().st_size / 1048576
    if size_mb > 24:
        mp3.unlink(missing_ok=True)
        raise RuntimeError(f"Audio {size_mb:.0f}MB melebihi limit upload Groq (~25MB) — pakai lokal")

    if progress_cb:
        progress_cb(20)
    data = {
        "model": model or "whisper-large-v3-turbo",
        "response_format": "verbose_json",
        "timestamp_granularities[]": "word",
    }
    if language in ("id", "en"):
        data["language"] = language
    with open(mp3, "rb") as f:
        resp = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data=data,
            files={"file": (mp3.name, f, "audio/mpeg")},
            timeout=300,
        )
    mp3.unlink(missing_ok=True)
    if resp.status_code != 200:
        raise RuntimeError(f"Groq API {resp.status_code}: {resp.text[:300]}")
    if progress_cb:
        progress_cb(85)

    payload = resp.json()
    words = []
    for w in (payload.get("words") or []):
        token = (w.get("word") or "").strip()
        if not token:
            continue
        words.append({
            "word": token,
            "start": round(float(w.get("start") or 0), 3),
            "end": round(float(w.get("end") or 0), 3),
            "conf": 1.0,
            "is_filler": _clean(token) in FILLER_WORDS,
        })
    for i in range(1, len(words)):
        if words[i]["start"] < words[i - 1]["end"]:
            words[i]["start"] = words[i - 1]["end"]
        if words[i]["end"] < words[i]["start"]:
            words[i]["end"] = words[i]["start"] + 0.05
    if progress_cb:
        progress_cb(99)
    lang = (payload.get("language") or language or "id").lower()
    lang = {"english": "en", "indonesian": "id"}.get(lang, lang[:2])
    return {"language": lang, "words": words}


def words_to_sentences(words: list) -> list:
    """
    Kelompokkan kata jadi kalimat utk prompt AI curation.
    Return [{"text", "start", "end"}]. Pemisah: tanda baca akhir kalimat atau jeda > 1 detik.
    """
    sentences = []
    cur = []
    for i, w in enumerate(words):
        cur.append(w)
        token = w["word"].strip()
        gap = (words[i + 1]["start"] - w["end"]) if i + 1 < len(words) else 99
        if re.search(r"[.!?…]$", token) or gap > 1.0 or len(cur) >= 40:
            sentences.append({
                "text": " ".join(x["word"].strip() for x in cur),
                "start": cur[0]["start"],
                "end": cur[-1]["end"],
            })
            cur = []
    if cur:
        sentences.append({
            "text": " ".join(x["word"].strip() for x in cur),
            "start": cur[0]["start"],
            "end": cur[-1]["end"],
        })
    return sentences
