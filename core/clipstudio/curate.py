"""
Tahap C — AI Curation: pilih segmen klip viral dari transkrip (Claude/Qwen).

Output per klip: {start, end, title, score, reason, hashtags} dengan start/end
SUDAH di-snap ke boundary kata terdekat dari data Whisper.
Fallback bila AI gagal: potong otomatis ~45 detik di batas kalimat.
"""

import json
import logging

from core.clipstudio.ai import llm_complete, extract_json
from core.clipstudio.transcribe import words_to_sentences

logger = logging.getLogger(__name__)

DURATION_TARGETS = {
    "<30":   (10, 30),
    "30-60": (30, 60),
    "60-90": (60, 90),
    "auto":  (20, 90),
}


def _snap_to_word_boundary(t: float, words: list, prefer: str) -> float:
    """Snap waktu ke boundary kata terdekat. prefer='start' -> awal kata, 'end' -> akhir kata."""
    if not words:
        return t
    best, best_d = t, 1e9
    for w in words:
        v = w["start"] if prefer == "start" else w["end"]
        d = abs(v - t)
        if d < best_d:
            best, best_d = v, d
    return round(best, 3)


def _fallback_segments(words: list, dur_range: tuple, max_clips: int) -> list:
    """Potong otomatis per ~45 detik di batas kalimat (fallback tanpa AI)."""
    lo, hi = dur_range
    target = min(hi, max(lo, 45))
    sentences = words_to_sentences(words)
    segs, cur_start, last_end = [], None, None
    for s in sentences:
        if cur_start is None:
            cur_start = s["start"]
        last_end = s["end"]
        if last_end - cur_start >= target:
            segs.append({
                "start": cur_start, "end": last_end,
                "title": "Klip otomatis " + str(len(segs) + 1),
                "score": 50, "reason": "Potongan otomatis di batas kalimat (AI tidak tersedia).",
                "hashtags": ["#shorts", "#viral", "#fyp"],
            })
            cur_start = None
            if len(segs) >= max_clips:
                break
    if cur_start is not None and last_end and last_end - cur_start >= lo and len(segs) < max_clips:
        segs.append({
            "start": cur_start, "end": last_end,
            "title": "Klip otomatis " + str(len(segs) + 1),
            "score": 50, "reason": "Potongan otomatis di batas kalimat (AI tidak tersedia).",
            "hashtags": ["#shorts", "#viral", "#fyp"],
        })
    return segs


def curate_clips(words: list, language: str, clip_length: str = "auto",
                 max_clips: int = 10) -> list:
    """Pilih segmen klip viral. Return list {start,end,title,score,reason,hashtags}."""
    dur_range = DURATION_TARGETS.get(clip_length, DURATION_TARGETS["auto"])
    lo, hi = dur_range
    if not words:
        return []

    sentences = words_to_sentences(words)
    transcript_lines = "\n".join(
        f"[{s['start']:.2f} - {s['end']:.2f}] {s['text']}" for s in sentences
    )
    lang_label = "bahasa Indonesia" if (language or "id").startswith("id") else "English"

    system = (
        "Kamu adalah editor video viral profesional (seperti Opus Clip). "
        "Tugasmu memilih segmen terbaik dari transkrip video untuk dijadikan klip pendek viral. "
        "Jawab HANYA dengan JSON murni tanpa teks lain."
    )
    user = (
        f"Transkrip video (format [detik_mulai - detik_selesai] teks):\n\n{transcript_lines}\n\n"
        f"Pilih MAKSIMAL {max_clips} segmen berdurasi {lo}-{hi} detik. Syarat tiap segmen:\n"
        f"- Punya HOOK kuat di 3 detik pertama.\n"
        f"- Satu ide utuh — JANGAN memotong di tengah kalimat; gunakan batas waktu kalimat di atas.\n"
        f"- Beri skor viralitas 0-100 beserta alasan singkat (hook kuat / topik tren / punchline).\n"
        f"- Judul clickbait dalam {lang_label}.\n"
        f"- 3 hashtag relevan.\n\n"
        f'Output JSON murni: [{{"start": 12.40, "end": 58.92, "title": "...", "score": 87, '
        f'"reason": "...", "hashtags": ["#a", "#b", "#c"]}}]'
    )

    raw = llm_complete(system, user, max_tokens=4000)
    data = extract_json(raw) if raw else None

    segs = []
    if isinstance(data, list):
        for item in data:
            try:
                start = float(item["start"])
                end = float(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                continue
            # Validasi & snap ke boundary kata terdekat (wajib sesuai spec)
            start = _snap_to_word_boundary(start, words, "start")
            end = _snap_to_word_boundary(end, words, "end")
            if end - start < max(5, lo * 0.5) or end - start > hi * 1.5:
                continue
            segs.append({
                "start": start, "end": end,
                "title": str(item.get("title") or "Klip Viral")[:300],
                "score": max(0, min(100, int(item.get("score") or 50))),
                "reason": str(item.get("reason") or "")[:1000],
                "hashtags": [str(h) for h in (item.get("hashtags") or [])][:5],
            })

    if not segs:
        logger.warning("[ClipStudio] AI curation kosong/gagal — pakai fallback per ~45s.")
        segs = _fallback_segments(words, dur_range, max_clips)

    segs.sort(key=lambda x: -x["score"])
    return segs[:max_clips]
