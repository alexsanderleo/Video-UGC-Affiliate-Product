"""
FASE 4/5 — Export final klip via ffmpeg, hasil identik dengan preview editor.

Rangkaian (sesuai spec 5.6):
(a) potongan video minus cut_ranges  -> trim/atrim + concat
(b) crop keyframes (speaker track)   -> sendcmd + crop (x,y dinamis per waktu)
(c) overlay media, B-roll, text      -> overlay enable=between(t,..), drawtext
(d) burn caption ASS karaoke         -> subtitles=...:fontsdir=static/fonts
(e) audio mix + ducking musik        -> sidechaincompress + amix
"""

import json
import logging
import re
import subprocess
import urllib.request
from pathlib import Path

from core.config import get_settings
from core.clipstudio.captions import build_ass, merge_style
from core.clipstudio.paths import clip_dir, project_dir, rel_storage, STORAGE_DIR
from core.clipstudio.reframe import crop_window

logger = logging.getLogger(__name__)
settings = get_settings()

RESOLUTIONS = {
    ("9:16", "1080p"): (1080, 1920), ("9:16", "720p"): (720, 1280),
    ("1:1", "1080p"): (1080, 1080), ("1:1", "720p"): (720, 720),
    ("16:9", "1080p"): (1920, 1080), ("16:9", "720p"): (1280, 720),
}


# ---------- timeline helpers ----------

def kept_segments(start: float, end: float, cut_ranges: list) -> list:
    """Rentang [start,end] dikurangi cut_ranges -> [(s,e), ...] terurut."""
    cuts = sorted([(max(start, float(a)), min(end, float(b)))
                   for a, b in (cut_ranges or []) if float(b) > start and float(a) < end])
    merged = []
    for a, b in cuts:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    segs, cur = [], start
    for a, b in merged:
        if a > cur:
            segs.append((cur, a))
        cur = max(cur, b)
    if end > cur + 0.01:
        segs.append((cur, end))
    return [(s, e) for s, e in segs if e - s > 0.04]


def src_to_out(t: float, segs: list) -> float | None:
    """Waktu sumber -> waktu output (None bila berada di dalam potongan)."""
    acc = 0.0
    for s, e in segs:
        if t < s:
            return None
        if t <= e:
            return acc + (t - s)
        acc += e - s
    return None


def out_to_src(t: float, segs: list) -> float:
    acc = 0.0
    for s, e in segs:
        d = e - s
        if t <= acc + d:
            return s + (t - acc)
        acc += d
    return segs[-1][1] if segs else t


def interp_center(keyframes: list, t: float) -> tuple:
    """Interpolasi linear pusat crop pada waktu sumber t."""
    if not keyframes:
        return (0.0, 0.0)
    kfs = sorted(keyframes, key=lambda k: k["t"])
    if t <= kfs[0]["t"]:
        return (kfs[0]["cx"], kfs[0]["cy"])
    for i in range(1, len(kfs)):
        if t <= kfs[i]["t"]:
            a, b = kfs[i - 1], kfs[i]
            f = (t - a["t"]) / max(1e-6, b["t"] - a["t"])
            return (a["cx"] + (b["cx"] - a["cx"]) * f, a["cy"] + (b["cy"] - a["cy"]) * f)
    return (kfs[-1]["cx"], kfs[-1]["cy"])


def _esc_fpath(p) -> str:
    """Escape path untuk argumen filter ffmpeg (Windows-safe)."""
    return str(p).replace("\\", "/").replace(":", "\\:")


def _esc_drawtext(s: str) -> str:
    return s.replace("\\", "").replace("'", "’").replace(":", "\\:").replace("%", "\\%")


def _has_audio(path: Path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                        "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return "audio" in (r.stdout or "")


def _resolve_local(url_or_path: str, dl_dir: Path) -> Path | None:
    """URL /storage/... atau http(s) -> path file lokal (download bila remote)."""
    if not url_or_path:
        return None
    if url_or_path.startswith("/storage/"):
        p = STORAGE_DIR / url_or_path[len("/storage/"):]
        return p if p.exists() else None
    if url_or_path.startswith("/backsounds/"):
        p = settings.BASE_DIR / url_or_path.lstrip("/")
        return p if p.exists() else None
    if url_or_path.startswith("http"):
        dl_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", url_or_path.split("/")[-1].split("?")[0])[-80:]
        dest = dl_dir / (name or "asset.bin")
        if not dest.exists():
            try:
                req = urllib.request.Request(url_or_path, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
                    f.write(r.read())
            except Exception as e:
                logger.warning("[Export] gagal download aset %s: %s", url_or_path, e)
                return None
        return dest
    p = Path(url_or_path)
    return p if p.exists() else None


# ---------- caption words ----------

def caption_words_for_export(words: list, clip_start: float, clip_end: float,
                             segs: list, edit_state: dict) -> list:
    """Kata transkrip dalam klip -> timeline output, terapkan edit teks & penghapusan."""
    from core.clipstudio.captions import mask_word
    es = edit_state or {}
    deleted = set(es.get("deleted_words") or [])
    censored = set(es.get("censored_words") or [])
    word_edits = {int(k): v for k, v in (es.get("word_edits") or {}).items()}
    out = []
    for idx, w in enumerate(words):
        if w["end"] <= clip_start or w["start"] >= clip_end:
            continue
        if idx in deleted:
            continue
        mid = (w["start"] + w["end"]) / 2
        t0, t1 = src_to_out(w["start"], segs), src_to_out(w["end"], segs)
        if t0 is None and t1 is None:
            if src_to_out(mid, segs) is None:
                continue
        if t0 is None:
            t0 = src_to_out(mid, segs) or 0.0
        if t1 is None or t1 <= t0:
            t1 = t0 + max(0.08, w["end"] - w["start"])
        text = word_edits.get(idx, w["word"])
        if not str(text).strip():
            continue
        if idx in censored:
            text = mask_word(str(text))   # auto censor: caption tersensor (k****)
        out.append({"word": str(text), "start": round(t0, 3), "end": round(t1, 3), "idx": idx})
    return out


def censor_out_ranges(words: list, edit_state: dict, segs: list) -> list:
    """Rentang waktu OUTPUT yang harus di-mute (kata tersensor)."""
    out = []
    for idx in (edit_state or {}).get("censored_words") or []:
        if not (0 <= int(idx) < len(words)):
            continue
        w = words[int(idx)]
        t0, t1 = src_to_out(w["start"], segs), src_to_out(w["end"], segs)
        if t0 is None or t1 is None or t1 <= t0:
            continue
        out.append((max(0, t0 - 0.03), t1 + 0.03))
    return out


def _prepend_append_cards(main_file: Path, work: Path, es: dict,
                          out_w: int, out_h: int) -> Path:
    """Brand intro/outro: gambar (1.8 dtk) atau video dinormalisasi lalu di-concat."""
    intro = _resolve_local((es.get("brand") or {}).get("intro"), work)
    outro = _resolve_local((es.get("brand") or {}).get("outro"), work)
    if not intro and not outro:
        return main_file

    def normalize(src: Path, tag: str) -> Path | None:
        dst = work / f"card_{tag}.mp4"
        is_img = src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        if is_img:
            inputs = ["-loop", "1", "-t", "1.8", "-i", str(src),
                      "-f", "lavfi", "-t", "1.8", "-i", "anullsrc=r=48000:cl=stereo"]
            amap = "[1:a]anull[a]"
        else:
            inputs = ["-i", str(src), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            # pakai audio asli bila ada; bila tidak, silent track menjadi fallback via amix
            amap = "[0:a]anull[a]" if _has_audio(src) else "[1:a]anull[a]"
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex",
               f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
               f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30[v];{amap}",
               "-map", "[v]", "-map", "[a]",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
               "-shortest", str(dst)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not dst.exists():
            logger.warning("[Export] normalisasi kartu %s gagal — dilewati", tag)
            return None
        return dst

    parts = []
    if intro:
        p = normalize(intro, "intro")
        if p:
            parts.append(p)
    parts.append(main_file)
    if outro:
        p = normalize(outro, "outro")
        if p:
            parts.append(p)
    if len(parts) == 1:
        return main_file

    listfile = work / "concat.txt"
    listfile.write_text("\n".join(f"file '{str(p).replace(chr(92), '/')}'" for p in parts),
                        encoding="utf-8")
    final = main_file.with_name(main_file.stem + "_final.mp4")
    r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
                        "-c", "copy", str(final)], capture_output=True, text=True)
    if r.returncode != 0 or not final.exists():
        # codec mismatch -> re-encode
        r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", str(final)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            logger.warning("[Export] concat intro/outro gagal — pakai video utama saja")
            return main_file
    final.replace(main_file)
    return main_file


def _merged_fontsdir(work: Path) -> Path:
    """Gabung static/fonts + storage/fonts (font custom user) untuk fontsdir ffmpeg."""
    import shutil
    fdir = work / "fonts"
    fdir.mkdir(exist_ok=True)
    for src_dir in (settings.BASE_DIR / "static" / "fonts", STORAGE_DIR / "fonts"):
        if src_dir.exists():
            for f in src_dir.glob("*.[ot]tf"):
                try:
                    shutil.copy2(f, fdir / f.name)
                except Exception:
                    pass
    return fdir


# ---------- main render ----------

def render_clip_export(project, clip, export, words: list, progress_cb=None) -> Path:
    """Render final satu klip. Return path file mp4."""
    es = clip.edit_state or {}
    style = merge_style(clip.caption_style)
    aspect = clip.aspect_ratio or "9:16"
    out_w, out_h = RESOLUTIONS.get((aspect, export.resolution), (1080, 1920))

    pdir = project_dir(project.id)
    cdir = clip_dir(project.id, clip.id)
    work = cdir / f"work_{export.id}"
    work.mkdir(parents=True, exist_ok=True)
    source = pdir / "source.mp4"

    clip_start = float(es.get("extend", {}).get("start", clip.start))
    clip_end = float(es.get("extend", {}).get("end", clip.end))
    segs = kept_segments(clip_start, clip_end, es.get("cut_ranges") or [])
    if not segs:
        raise ValueError("Seluruh klip terpotong — tidak ada segmen tersisa untuk diekspor.")
    out_duration = sum(e - s for s, e in segs)

    src_w, src_h = project.width or 1920, project.height or 1080

    # --- (b) sendcmd file: crop dinamis mengikuti keyframes (waktu output) ---
    keyframes = es.get("crop_keyframes") or clip.crop_keyframes or []
    if not clip.tracker_on or not keyframes:
        keyframes = [{"t": clip_start, "cx": src_w / 2, "cy": src_h / 2}]
    _, _, cw, ch = crop_window(src_w / 2, src_h / 2, src_w, src_h, aspect)

    cmd_lines = []
    t = 0.0
    while t <= out_duration + 0.001:
        src_t = out_to_src(t, segs)
        cx, cy = interp_center(keyframes, src_t)
        x, y, _, _ = crop_window(cx, cy, src_w, src_h, aspect)
        cmd_lines.append(f"{t:.2f} crop x {x};")
        cmd_lines.append(f"{t:.2f} crop y {y};")
        t += 0.2
    sendcmd_file = work / "crop_cmd.txt"
    sendcmd_file.write_text("\n".join(cmd_lines), encoding="utf-8")

    # --- (d) ASS caption (timeline output) ---
    show_captions = (es.get("captions_on") is not False)
    ass_file = None
    if show_captions and words:
        cap_words = caption_words_for_export(words, clip_start, clip_end, segs, es)
        if cap_words:
            kw = {int(k): v for k, v in (es.get("keyword_colors") or {}).items()}
            if "pos_pct" in es:
                style["pos_pct"] = es["pos_pct"]
            ass_text = build_ass(cap_words, style, out_w, out_h, kw)
            ass_file = work / "captions.ass"
            ass_file.write_text(ass_text, encoding="utf-8")

    # --- inputs ---
    # Item visual digabung & diurutkan mengikuti TRACK timeline (CapCut):
    # track kecil = layer bawah. Default: broll=0, media/stiker=1, teks/efek=2.
    inputs = ["-i", str(source)]
    input_idx = 1
    ops = []   # campuran: {"op":"overlay", in_idx, item, kind, track} / {"op":"text", item, track}

    def _track(item, default):
        try:
            return int(item.get("track", default))
        except (TypeError, ValueError):
            return default

    visual = ([("broll", it, _track(it, 0)) for it in (es.get("broll") or [])] +
              [("media", it, _track(it, 1)) for it in (es.get("overlays") or [])
               if (it.get("type") or "") != "audio"])
    visual.sort(key=lambda v: (v[2], float(v[1].get("start") or 0)))

    for group, item, track in visual:
        p = _resolve_local(item.get("url") or item.get("path"), work)
        if not p:
            continue
        kind = item.get("type") or ("video" if p.suffix.lower() in (".mp4", ".webm", ".mov") else "image")
        if kind == "image":
            inputs += ["-loop", "1", "-t", str(out_duration), "-i", str(p)]
        else:
            inputs += ["-stream_loop", "0", "-i", str(p)]
        ops.append({"op": "overlay", "in_idx": input_idx, "item": item,
                    "kind": f"{group}-{kind}", "track": track})
        input_idx += 1

    for txt in (es.get("texts") or []):
        ops.append({"op": "text", "item": txt, "track": _track(txt, 2)})
    ops.sort(key=lambda o: (o["track"], float(o["item"].get("start") or 0)))

    music = es.get("music") or {}
    music_path = _resolve_local(music.get("url") or music.get("path"), work) if music else None
    music_idx = None
    if music_path:
        inputs += ["-stream_loop", "-1", "-i", str(music_path)]
        music_idx = input_idx
        input_idx += 1

    # AI voice-over + audio tambahan (multi-track ala add_audios): volume & fade per item
    voiceover_specs = []
    for vo in list(es.get("voiceovers") or []) + list(es.get("audios") or []):
        p = _resolve_local(vo.get("url") or vo.get("path"), work)
        if not p:
            continue
        inputs += ["-i", str(p)]
        voiceover_specs.append((input_idx, vo))
        input_idx += 1

    # --- filtergraph ---
    fg = []
    transitions = es.get("transitions") or []
    trans_type = (transitions[0].get("type") if transitions else "cut") if isinstance(transitions, list) else "cut"
    fade_d = 0.12

    vparts, aparts = [], []
    for i, (s, e) in enumerate(segs):
        vf = f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS"
        af = f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS"
        if trans_type != "cut" and len(segs) > 1:
            d = e - s
            fades = []
            if i > 0:
                fades.append(f"fade=t=in:st=0:d={fade_d}")
            if i < len(segs) - 1:
                fades.append(f"fade=t=out:st={max(0, d - fade_d):.3f}:d={fade_d}")
            if fades:
                vf += "," + ",".join(fades)
        fg.append(vf + f"[v{i}]")
        fg.append(af + f"[a{i}]")
        vparts.append(f"[v{i}]")
        aparts.append(f"[a{i}]")
    fg.append("".join(vparts) + f"concat=n={len(segs)}:v=1:a=0[vcat]")
    fg.append("".join(aparts) + f"concat=n={len(segs)}:v=0:a=1[acat]")

    # layout: fill (crop track) / fit (blur bg) / split (video atas, area caption bawah)
    layout = (clip.layout_mode or "fill").lower()
    if layout == "fit":
        fg.append(
            f"[vcat]split=2[vbg][vfg];"
            f"[vbg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
            f"crop={out_w}:{out_h},boxblur=20:5[bg];"
            f"[vfg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease[fg0];"
            f"[bg][fg0]overlay=(W-w)/2:(H-h)/2[vbase]"
        )
    elif layout == "split":
        vh = int(out_h * 0.55)
        fg.append(
            f"[vcat]crop={src_w}:{int(src_w * vh / out_w)}:0:(ih-oh)/2,scale={out_w}:{vh}[vtop];"
            f"color=c=0x101014:s={out_w}x{out_h}:d={out_duration:.3f}[cbg];"
            f"[cbg][vtop]overlay=0:0[vbase]"
        )
    else:  # fill — crop dinamis mengikuti pembicara
        fg.append(
            f"[vcat]sendcmd=f='{_esc_fpath(sendcmd_file)}',"
            f"crop={cw}:{ch}:{crop_window(*interp_center(keyframes, out_to_src(0, segs)), src_w, src_h, aspect)[0]}:"
            f"{crop_window(*interp_center(keyframes, out_to_src(0, segs)), src_w, src_h, aspect)[1]},"
            f"scale={out_w}:{out_h}[vbase]"
        )

    cur = "vbase"
    n_ov = 0

    # --- Efek (CapCut-style) — diterapkan ke video dasar SEBELUM overlay ---
    eff_filters = []
    shake_windows = []
    for fx in (es.get("effects") or []):
        t0 = float(fx.get("start") or 0)
        t1 = float(fx.get("end") or t0 + 3)
        en = f":enable='between(t,{t0:.3f},{t1:.3f})'"
        ft = (fx.get("type") or "").lower()
        if ft == "bw":
            eff_filters.append(f"hue=s=0{en}")
        elif ft == "vintage":
            eff_filters.append(f"eq=saturation=0.6:contrast=1.08:brightness=0.02{en}")
        elif ft == "blur":
            eff_filters.append(f"gblur=sigma=10{en}")
        elif ft == "glow":
            eff_filters.append(f"eq=brightness=0.12:saturation=1.35{en}")
        elif ft == "grain":
            eff_filters.append(f"noise=alls=12:allf=t+u{en}")
        elif ft == "invert":
            eff_filters.append(f"negate{en}")
        elif ft == "shake":
            shake_windows.append((t0, t1))
    if shake_windows:
        wins = "+".join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in shake_windows)
        eff_filters.append(
            f"crop=iw-16:ih-16:x='8+if({wins}\\,sin(t*53)*7\\,0)':y='8+if({wins}\\,cos(t*47)*7\\,0)',"
            f"scale={out_w}:{out_h}"
        )
    if eff_filters:
        fg.append(f"[{cur}]" + ",".join(eff_filters) + "[vfx]")
        cur = "vfx"

    # --- Overlay & teks, urut TRACK (layer bawah dulu) ---
    for op in ops:
        item = op["item"]
        ts = float(item.get("start") or 0)
        te = float(item.get("end") or min(out_duration, ts + 3))
        n_ov += 1
        lbl = f"ov{n_ov}"
        # animasi masuk/keluar (buatan sendiri — setara preview)
        anim_in = (item.get("anim_in") or "none").lower()
        anim_out = (item.get("anim_out") or "none").lower()
        anim_loop = (item.get("anim_loop") or "none").lower()
        adur = min(0.45, max(0.1, (te - ts) / 2))

        if op["op"] == "overlay":
            in_idx, kind = op["in_idx"], op["kind"]
            # rantai pra-proses overlay: scale -> mask -> rotasi -> border -> fade alpha
            pre = []
            if kind.startswith("broll"):
                pre.append(f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,crop={out_w}:{out_h}")
            else:
                ow = int(out_w * (float(item.get("w_pct") or 40) / 100))
                pre.append(f"scale={ow}:-2")
            mask = (item.get("mask") or "none").lower()
            if mask in ("circle", "rounded"):
                pre.append("format=rgba")
                if mask == "circle":
                    pre.append("geq=a='if(lte(hypot(X-W/2,Y-H/2),min(W,H)/2),alpha(X,Y),0)'"
                               ":r='r(X,Y)':g='g(X,Y)':b='b(X,Y)'")
                else:  # rounded rect (radius 14%)
                    pre.append("geq=a='if(lte(hypot(max(max(0.14*min(W,H)-X,X-(W-0.14*min(W,H))),0),"
                               "max(max(0.14*min(W,H)-Y,Y-(H-0.14*min(W,H))),0)),0.14*min(W,H)),alpha(X,Y),0)'"
                               ":r='r(X,Y)':g='g(X,Y)':b='b(X,Y)'")
            if item.get("border"):
                bc = (item.get("border_color") or "#FFFFFF").replace("#", "0x")
                pre.append(f"pad=iw+8:ih+8:4:4:color={bc}")
            rot = float(item.get("rot") or 0)
            if rot:
                import math as _math
                pre.append(f"format=rgba,rotate={rot * _math.pi / 180:.5f}:c=none:ow=rotw({rot * _math.pi / 180:.5f}):oh=roth({rot * _math.pi / 180:.5f})")
            pre.append(f"setpts=PTS-STARTPTS+{ts:.3f}/TB")
            if anim_in in ("fade", "slide-up", "slide-down", "slide-left"):
                pre.append(f"format=rgba,fade=t=in:st={ts:.3f}:d={adur:.2f}:alpha=1")
            if anim_out in ("fade", "slide-down"):
                pre.append(f"format=rgba,fade=t=out:st={te - adur:.3f}:d={adur:.2f}:alpha=1")
            fg.append(f"[{in_idx}:v]" + ",".join(pre) + f"[m{n_ov}]")

            # posisi (dengan offset slide bila ada)
            if kind.startswith("broll"):
                xe, ye = "0", "0"
            else:
                xpct = float(item.get("x_pct") or 50) / 100
                ypct = float(item.get("y_pct") or 50) / 100
                xe = f"{int(out_w * xpct)}-w/2"
                ye = f"{int(out_h * ypct)}-h/2"
            slide = int(out_h * 0.07)
            if anim_in == "slide-up":
                ye += f"+if(lt(t,{ts + adur:.3f}),(1-(t-{ts:.3f})/{adur:.2f})*{slide},0)"
            elif anim_in == "slide-down":
                ye += f"-if(lt(t,{ts + adur:.3f}),(1-(t-{ts:.3f})/{adur:.2f})*{slide},0)"
            elif anim_in == "slide-left":
                xe += f"+if(lt(t,{ts + adur:.3f}),(1-(t-{ts:.3f})/{adur:.2f})*{int(out_w * 0.12)},0)"
            if anim_out == "slide-down":
                ye += f"+if(gt(t,{te - adur:.3f}),(1-({te:.3f}-t)/{adur:.2f})*{slide},0)"
            fg.append(f"[{cur}][m{n_ov}]overlay=x='{xe}':y='{ye}'"
                      f":enable='between(t,{ts:.3f},{te:.3f})'[{lbl}]")
        else:  # text — animasi via ekspresi alpha & y drawtext
            content = _esc_drawtext(str(item.get("text") or "")[:200])
            if not content:
                n_ov -= 1
                continue
            size = int(item.get("size") or 56) * out_h // 1920
            color = (item.get("color") or "#FFFFFF").replace("#", "0x")
            ypct = float(item.get("y_pct") or 12) / 100
            fontfile = settings.BASE_DIR / "static" / "fonts" / "arialbd.ttf"
            # alpha: fade in/out + loop pulse
            a_in = f"if(lt(t,{ts + adur:.3f}),(t-{ts:.3f})/{adur:.2f},1)" if anim_in != "none" else "1"
            a_out = f"if(gt(t,{te - adur:.3f}),({te:.3f}-t)/{adur:.2f},1)" if anim_out != "none" else "1"
            a_loop = "(0.82+0.18*sin(t*6))" if anim_loop == "pulse" else "1"
            alpha = f"{a_in}*{a_out}*{a_loop}"
            ybase = int(out_h * ypct)
            slide = int(out_h * 0.07)
            ye = f"{ybase}"
            if anim_in == "slide-up":
                ye += f"+if(lt(t,{ts + adur:.3f}),(1-(t-{ts:.3f})/{adur:.2f})*{slide},0)"
            elif anim_in == "slide-down":
                ye += f"-if(lt(t,{ts + adur:.3f}),(1-(t-{ts:.3f})/{adur:.2f})*{slide},0)"
            if anim_out == "slide-down":
                ye += f"+if(gt(t,{te - adur:.3f}),(1-({te:.3f}-t)/{adur:.2f})*{slide},0)"
            xe = "(w-text_w)/2"
            if anim_in == "slide-left":
                xe += f"+if(lt(t,{ts + adur:.3f}),(1-(t-{ts:.3f})/{adur:.2f})*{int(out_w * 0.12)},0)"
            fg.append(
                f"[{cur}]drawtext=fontfile='{_esc_fpath(fontfile)}':text='{content}'"
                f":fontsize={size}:fontcolor={color}:borderw=3:bordercolor=black"
                f":alpha='{alpha}'"
                f":x='{xe}':y='{ye}'"
                f":enable='between(t,{ts:.3f},{te:.3f})'[{lbl}]"
            )
        cur = lbl

    # (d) burn caption (fontsdir = static/fonts + font custom user)
    if ass_file:
        fontsdir = _merged_fontsdir(work)
        n_ov += 1
        lbl = f"ov{n_ov}"
        fg.append(f"[{cur}]subtitles=filename='{_esc_fpath(ass_file)}':fontsdir='{_esc_fpath(fontsdir)}'[{lbl}]")
        cur = lbl

    # watermark opsional
    if export.watermark:
        fontfile = settings.BASE_DIR / "static" / "fonts" / "arialbd.ttf"
        n_ov += 1
        lbl = f"ov{n_ov}"
        fg.append(
            f"[{cur}]drawtext=fontfile='{_esc_fpath(fontfile)}':text='video.agomart.com'"
            f":fontsize={int(out_h * 0.022)}:fontcolor=white@0.55:borderw=2:bordercolor=black@0.4"
            f":x=w-text_w-24:y=24[{lbl}]"
        )
        cur = lbl

    # (e) audio: volume asli + censor mute + enhancement + voice-over + musik ducking
    vol = float(es.get("volume", 1.0))
    voice_chain = f"volume={vol:.2f}"
    # Auto censor: mute rentang kata tersensor
    cranges = censor_out_ranges(words, es, segs)
    if cranges:
        expr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in cranges)
        voice_chain += f",volume=enable='{expr}':volume=0"
    # AI speech enhancement: denoise + highpass + loudness normalize
    if es.get("audio_enhance"):
        voice_chain += ",highpass=f=70,afftdn=nf=-22,loudnorm=I=-16:TP=-1.5:LRA=11"
    fg.append(f"[acat]{voice_chain}[voice0]")

    # Mix voice-over & audio tambahan di atas suara asli (per item: volume + fade opsional)
    if voiceover_specs:
        vo_labels = []
        for k, (in_idx, vo) in enumerate(voiceover_specs):
            vstart = float(vo.get("start") or 0)
            vend = float(vo.get("end") or vstart + float(vo.get("duration") or 3))
            idur = max(0.2, vend - vstart)
            delay_ms = max(0, int(vstart * 1000))
            vvol = float(vo.get("volume", 1.0))
            chain = f"volume={vvol:.2f},atrim=0:{idur:.3f}"
            if vo.get("fade"):
                fdur = min(0.8, idur / 3)
                chain += (f",afade=t=in:st=0:d={fdur:.2f}"
                          f",afade=t=out:st={max(0, idur - fdur):.3f}:d={fdur:.2f}")
            chain += f",adelay={delay_ms}:all=1,apad,atrim=0:{out_duration:.3f}"
            fg.append(f"[{in_idx}:a]{chain}[vo{k}]")
            vo_labels.append(f"[vo{k}]")
        fg.append(f"[voice0]{''.join(vo_labels)}amix=inputs={1 + len(vo_labels)}"
                  f":duration=first:dropout_transition=0:normalize=0[voice]")
    else:
        fg.append("[voice0]anull[voice]")

    if music_idx is not None:
        # blok musik di timeline: hormati start/end (geser & resize dari editor)
        mvol = float(music.get("volume", 0.25))
        mstart = max(0.0, float(music.get("start") or 0))
        mend = float(music.get("end") or out_duration)
        mend = min(max(mend, mstart + 0.3), out_duration)
        mdur = mend - mstart
        mfade = ""
        if music.get("fade", True):
            fin = min(1.2, mdur / 3)
            fout = min(1.5, mdur / 3)
            mfade = (f",afade=t=in:st=0:d={fin:.2f}"
                     f",afade=t=out:st={max(0, mdur - fout):.2f}:d={fout:.2f}")
        fg.append(f"[{music_idx}:a]volume={mvol:.2f},atrim=0:{mdur:.3f}{mfade},"
                  f"adelay={int(mstart * 1000)}:all=1,apad,atrim=0:{out_duration:.3f}[mus]")
        if music.get("duck", True):
            fg.append("[voice]asplit=2[vc1][vc2]")
            fg.append("[mus][vc2]sidechaincompress=threshold=0.04:ratio=10:attack=30:release=400[musd]")
            fg.append("[vc1][musd]amix=inputs=2:duration=first:dropout_transition=0,alimiter=limit=0.95[aout]")
        else:
            fg.append("[voice][mus]amix=inputs=2:duration=first:dropout_transition=0[aout]")
    else:
        fg.append("[voice]anull[aout]")

    filter_complex = ";".join(fg)
    out_file = cdir / f"export_{export.id}.mp4"
    fg_file = work / "filtergraph.txt"
    fg_file.write_text(filter_complex, encoding="utf-8")

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex_script", str(fg_file),
        "-map", f"[{cur}]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20" if export.resolution == "1080p" else "23",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(out_file),
    ]
    logger.info("[Export %s] ffmpeg mulai (%d segmen, %.1fs)", export.id[:8], len(segs), out_duration)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    stderr_tail = []
    import threading

    def _drain_err():
        for line in proc.stderr:
            stderr_tail.append(line)
            if len(stderr_tail) > 60:
                stderr_tail.pop(0)

    threading.Thread(target=_drain_err, daemon=True).start()

    for line in proc.stdout:
        m = re.match(r"out_time_ms=(\d+)", line.strip())
        if m and progress_cb:
            pct = min(99, int(int(m.group(1)) / 1_000_000 / out_duration * 100))
            progress_cb(pct)
    proc.wait()
    if proc.returncode != 0 or not out_file.exists():
        raise RuntimeError("FFmpeg export gagal:\n" + "".join(stderr_tail[-25:]))

    # Brand intro/outro cards (Opus: Brand template)
    out_file = _prepend_append_cards(out_file, work, es, out_w, out_h)
    return out_file
