"""
Caption engine — template style + builder subtitle ASS karaoke per kata.

Preview di editor dirender frontend (canvas/HTML overlay) memakai definisi template
yang SAMA (di-serve via API), sehingga hasil burn ffmpeg identik dengan preview.

Teknik highlight per kata: satu event Dialogue per kata aktif (bukan \\k progresif),
karena gaya Opus hanya mewarnai KATA AKTIF — kata yang sudah lewat kembali ke warna
dasar. Event tambahan tanpa highlight mengisi jeda antar kata dalam satu page.
"""

import re

# 6+ template siap pakai (tiru gaya populer Opus/CapCut).
# Warna hex #RRGGBB. mode: highlight | box | pop | news | clean | brand
CAPTION_TEMPLATES = [
    {
        "id": "opus-green", "name": "Bold Hijau (Opus)", "mode": "highlight",
        "font": "Impact", "size": 64, "uppercase": True, "max_words": 4,
        "text_color": "#FFFFFF", "highlight_color": "#39FF14",
        "outline_color": "#000000", "outline": 4, "shadow": 1,
        "box_color": None, "pos_pct": 72,
    },
    {
        "id": "yellow-box", "name": "Kuning + Box", "mode": "box",
        "font": "Arial", "size": 58, "uppercase": True, "max_words": 4,
        "text_color": "#FFFFFF", "highlight_color": "#FFE600",
        "outline_color": "#000000", "outline": 0, "shadow": 0,
        "box_color": "#000000B0", "pos_pct": 72,
    },
    {
        "id": "word-pop", "name": "Pop Per Kata", "mode": "pop",
        "font": "Impact", "size": 76, "uppercase": True, "max_words": 1,
        "text_color": "#FFFFFF", "highlight_color": "#FF3CAC",
        "outline_color": "#000000", "outline": 4, "shadow": 2,
        "box_color": None, "pos_pct": 65,
    },
    {
        "id": "news-strip", "name": "Strip Berita", "mode": "news",
        "font": "Verdana", "size": 46, "uppercase": False, "max_words": 6,
        "text_color": "#FFFFFF", "highlight_color": "#FFD400",
        "outline_color": "#000000", "outline": 0, "shadow": 0,
        "box_color": "#C40000E0", "pos_pct": 88,
    },
    {
        "id": "clean", "name": "Clean Minimal", "mode": "clean",
        "font": "Trebuchet MS", "size": 54, "uppercase": False, "max_words": 5,
        "text_color": "#FFFFFF", "highlight_color": "#FFFFFF",
        "outline_color": "#000000", "outline": 2, "shadow": 1,
        "box_color": None, "pos_pct": 75,
    },
    {
        "id": "brand", "name": "Warna Brand", "mode": "highlight",
        "font": "Arial", "size": 60, "uppercase": True, "max_words": 4,
        "text_color": "#FFFFFF", "highlight_color": "#8B5CF6",
        "outline_color": "#1E1B4B", "outline": 4, "shadow": 1,
        "box_color": None, "pos_pct": 72,
    },
    {
        "id": "beasty-red", "name": "Beasty Merah", "mode": "highlight",
        "font": "Impact", "size": 70, "uppercase": True, "max_words": 3,
        "text_color": "#FFFFFF", "highlight_color": "#FF1A1A",
        "outline_color": "#000000", "outline": 5, "shadow": 2,
        "box_color": None, "pos_pct": 68,
    },
    {
        "id": "podcast-cyan", "name": "Podcast Cyan", "mode": "box",
        "font": "Verdana", "size": 52, "uppercase": False, "max_words": 5,
        "text_color": "#FFFFFF", "highlight_color": "#22D3EE",
        "outline_color": "#000000", "outline": 0, "shadow": 0,
        "box_color": "#0F172AC8", "pos_pct": 75,
    },
    {
        "id": "mrwhos-purple", "name": "Glow Ungu", "mode": "pop",
        "font": "Arial Black", "size": 72, "uppercase": True, "max_words": 2,
        "text_color": "#F4F4F6", "highlight_color": "#C084FC",
        "outline_color": "#3B0764", "outline": 4, "shadow": 2,
        "box_color": None, "pos_pct": 66,
    },
]

# Daftar kata yang dimask fitur Auto Censor (ID + EN). Export: audio di-mute,
# caption tampil tersensor (k****). User bisa menambah via edit manual.
CENSOR_WORDS = {
    # Indonesia
    "anjing", "bangsat", "babi", "kontol", "memek", "ngentot", "jancok", "jancuk",
    "asu", "goblok", "tolol", "bajingan", "kampret", "tai", "bego", "perek", "lonte",
    # English
    "fuck", "fucking", "fucked", "shit", "bitch", "asshole", "dick", "pussy",
    "bastard", "cunt", "motherfucker", "nigga", "whore", "slut",
}


def mask_word(token: str) -> str:
    """'kontol' -> 'k*****' (mempertahankan tanda baca akhir)."""
    core = token.strip()
    tail = ""
    while core and not core[-1].isalnum():
        tail = core[-1] + tail
        core = core[:-1]
    if len(core) <= 1:
        return "*" + tail
    return core[0] + "*" * (len(core) - 1) + tail

CAPTION_FONTS = ["Impact", "Arial", "Arial Black", "Verdana", "Trebuchet MS",
                 "Georgia", "Tahoma", "Comic Sans MS", "Courier New"]

PAGE_GAP_BREAK = 1.2  # jeda > 1.2s memulai page baru


def get_template(template_id: str) -> dict:
    for t in CAPTION_TEMPLATES:
        if t["id"] == template_id:
            return dict(t)
    return dict(CAPTION_TEMPLATES[0])


def merge_style(caption_style: dict | None) -> dict:
    """Gabungkan template dasar + override user (caption_style klip)."""
    base = get_template((caption_style or {}).get("template", "opus-green"))
    base.update({k: v for k, v in (caption_style or {}).items() if v is not None})
    return base


def group_pages(words: list, max_words: int = 4) -> list:
    """Kelompokkan kata jadi 'page' caption (1 baris berisi 3-5 kata)."""
    max_words = max(1, int(max_words or 4))
    pages, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt_gap = (words[i + 1]["start"] - w["end"]) if i + 1 < len(words) else 99
        token = w["word"].strip()
        if len(cur) >= max_words or nxt_gap > PAGE_GAP_BREAK or re.search(r"[.!?…]$", token):
            pages.append(cur)
            cur = []
    if cur:
        pages.append(cur)
    return pages


# ---------- ASS builder ----------

def _hex_to_ass(color: str, alpha: str = "00") -> str:
    """#RRGGBB[AA] -> &HAABBGGRR (ASS, AA=00 opaque)."""
    c = (color or "#FFFFFF").lstrip("#")
    if len(c) == 8:
        alpha = format(255 - int(c[6:8], 16), "02X")  # CSS alpha -> ASS alpha (terbalik)
        c = c[:6]
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha}{b}{g}{r}"


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def build_ass(words: list, style: dict, play_w: int, play_h: int,
              keyword_indices: dict | None = None) -> str:
    """
    Bangun subtitle ASS karaoke. `words` = kata dalam timeline video OUTPUT
    (sudah dikurangi cut_ranges & offset start klip): [{word,start,end,idx}].
    keyword_indices = {idx_kata: "#warna"} untuk keyword highlight AI.
    """
    style = merge_style(style)
    uppercase = bool(style.get("uppercase"))
    size = int(style.get("size", 60) * (play_h / 1920))  # skala relatif 1080x1920 basis
    pos_pct = float(style.get("pos_pct", 72))
    margin_v = int(play_h * (1 - pos_pct / 100))
    use_box = style.get("box_color") and style["mode"] in ("box", "news")

    primary = _hex_to_ass(style["text_color"])
    highlight = _hex_to_ass(style["highlight_color"])
    outline_c = _hex_to_ass(style["outline_color"])
    back_c = _hex_to_ass(style["box_color"] or "#000000A0") if use_box else _hex_to_ass("#000000")
    border_style = 4 if use_box else 1
    outline_w = int(style.get("outline", 3)) if not use_box else 6

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_w}
PlayResY: {play_h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{style['font']},{size},{primary},{primary},{outline_c},{back_c},-1,0,0,0,100,100,0,0,{border_style},{outline_w},{int(style.get('shadow', 1))},2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    mode = style.get("mode", "highlight")
    pages = group_pages(words, style.get("max_words", 4))
    kw = keyword_indices or {}

    def render_text(page, active_i=None):
        parts = []
        for j, w in enumerate(page):
            txt = _esc(w["word"].strip())
            if uppercase:
                txt = txt.upper()
            widx = w.get("idx")
            if active_i is not None and j == active_i:
                if mode == "pop":
                    parts.append(f"{{\\c{highlight}\\fscx115\\fscy115}}{txt}{{\\r}}")
                else:
                    parts.append(f"{{\\c{highlight}\\b1}}{txt}{{\\r}}")
            elif widx is not None and widx in kw:
                parts.append(f"{{\\c{_hex_to_ass(kw[widx])}}}{txt}{{\\r}}")
            else:
                parts.append(txt)
        return " ".join(parts)

    for page in pages:
        if mode == "pop":
            # Tampilkan hanya kata aktif, satu-satu
            for w in page:
                if w["end"] <= w["start"]:
                    continue
                events.append(
                    f"Dialogue: 0,{_ts(w['start'])},{_ts(w['end'])},Cap,,0,0,0,,"
                    f"{render_text([w], 0)}"
                )
            continue
        # Mode lain: page tampil dari kata pertama s/d kata terakhir,
        # dipecah per kata aktif + segmen jeda tanpa highlight.
        for j, w in enumerate(page):
            if w["end"] > w["start"]:
                events.append(
                    f"Dialogue: 0,{_ts(w['start'])},{_ts(w['end'])},Cap,,0,0,0,,"
                    f"{render_text(page, j)}"
                )
            nxt = page[j + 1] if j + 1 < len(page) else None
            if nxt and nxt["start"] - w["end"] > 0.05:
                events.append(
                    f"Dialogue: 0,{_ts(w['end'])},{_ts(nxt['start'])},Cap,,0,0,0,,"
                    f"{render_text(page, None)}"
                )

    return header + "\n".join(events) + "\n"
