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
                 max_clips: int = 10, prompt: str = "") -> list:
    """
    Pilih segmen klip viral. Return list {start,end,title,score,reason,hashtags,breakdown}.
    prompt = instruksi ClipAnything dari user (mis. "cari momen tentang tips bisnis")
    breakdown = komponen skor ala Opus: {hook, flow, value, trend} masing-masing 0-100.
    """
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
    prompt_line = ""
    if (prompt or "").strip():
        prompt_line = (
            f"\nINSTRUKSI KHUSUS DARI USER (ClipAnything — WAJIB diprioritaskan): "
            f"\"{prompt.strip()[:500]}\". Pilih hanya momen yang sesuai instruksi ini.\n"
        )

    # Kriteria pemilihan bisa di-custom dari ADMIN PANEL (app_settings.clip_curate_criteria).
    # Kontrak output JSON tetap dikunci di bawah agar parsing tidak pernah rusak.
    from core.clipstudio.settings_store import DEFAULT_CRITERIA, get_clip_settings
    criteria = (get_clip_settings().get("clip_curate_criteria") or "").strip() or DEFAULT_CRITERIA
    criteria = criteria.replace("{bahasa}", lang_label)

    user = (
        f"Transkrip video (format [detik_mulai - detik_selesai] teks):\n\n{transcript_lines}\n"
        f"{prompt_line}\n"
        f"Pilih MAKSIMAL {max_clips} segmen. DURASI TIAP SEGMEN WAJIB {lo}-{hi} DETIK "
        f"(end - start harus >= {lo} dan <= {hi}; gabungkan beberapa kalimat berurutan bila perlu "
        f"agar mencapai durasi minimum). Syarat tiap segmen:\n"
        f"{criteria}\n\n"
        f'Output JSON murni: [{{"start": 12.40, "end": 58.92, "title": "...", "score": 87, '
        f'"reason": "...", "hashtags": ["#a", "#b", "#c"], '
        f'"breakdown": {{"hook": 92, "flow": 85, "value": 80, "trend": 88}}}}]'
    )

    raw = llm_complete(system, user, max_tokens=4000)
    data = extract_json(raw) if raw else None
    if raw and data is None:
        logger.warning("[ClipStudio] respons LLM tidak bisa diparse JSON: %s", (raw or "")[:200])

    segs = []
    rejected = 0
    if isinstance(data, list):
        for item in data:
            try:
                start = float(item["start"])
                end = float(item["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                continue
            # Validasi & snap ke boundary kata terdekat (wajib sesuai spec).
            # Toleransi durasi longgar: AI kadang memberi segmen sedikit di luar target —
            # lebih baik diterima daripada jatuh ke fallback bodoh per-45 detik.
            start = _snap_to_word_boundary(start, words, "start")
            end = _snap_to_word_boundary(end, words, "end")
            if end - start < max(8, lo * 0.35) or end - start > hi * 2:
                rejected += 1
                continue
            score = max(0, min(100, int(item.get("score") or 50)))
            bd = item.get("breakdown") or {}
            breakdown = {k: max(0, min(100, int(bd.get(k) or score)))
                         for k in ("hook", "flow", "value", "trend")}
            segs.append({
                "start": start, "end": end,
                "title": str(item.get("title") or "Klip Viral")[:300],
                "score": score,
                "reason": str(item.get("reason") or "")[:1000],
                "hashtags": [str(h) for h in (item.get("hashtags") or [])][:5],
                "breakdown": breakdown,
            })

    if not segs:
        logger.warning(
            "[ClipStudio] AI curation kosong/gagal (raw=%s, parsed=%s, ditolak_durasi=%d) "
            "— pakai fallback per ~45s.",
            "ada" if raw else "None", type(data).__name__, rejected,
        )
        segs = _fallback_segments(words, dur_range, max_clips)
        for s in segs:
            s.setdefault("breakdown", {"hook": 50, "flow": 50, "value": 50, "trend": 50})

    segs.sort(key=lambda x: -x["score"])
    return segs[:max_clips]
