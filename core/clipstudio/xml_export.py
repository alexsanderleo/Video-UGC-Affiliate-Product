"""
Export to XML (FCP7 / xmeml) — kompatibel Adobe Premiere Pro & DaVinci Resolve.

Timeline berisi potongan klip (kept segments, sudah minus cut_ranges) yang
mereferensikan file sumber asli, sehingga editor pro bisa melanjutkan editing
non-destruktif (fitur "Export to XML" Opus).
"""

from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from core.clipstudio.export import kept_segments
from core.clipstudio.paths import project_dir


def _pathurl(p: Path) -> str:
    # file://localhost/D%3a/folder/file.mp4 (format yang dimengerti Premiere & Resolve)
    posix = p.resolve().as_posix()
    return "file://localhost/" + quote(posix, safe="/")


def build_xmeml(project, clip, edit_state: dict) -> str:
    es = edit_state or {}
    fps = int(round(project.fps or 30)) or 30
    clip_start = float(es.get("extend", {}).get("start", clip.start))
    clip_end = float(es.get("extend", {}).get("end", clip.end))
    segs = kept_segments(clip_start, clip_end, es.get("cut_ranges") or [])
    if not segs:
        segs = [(clip_start, clip_end)]

    src = project_dir(project.id) / "source.mp4"
    src_frames = int(round((project.duration or 0) * fps))
    w, h = project.width or 1920, project.height or 1080
    name = escape(clip.title or "Klip")

    file_def_done = False

    def file_node(indent: str) -> str:
        nonlocal file_def_done
        if file_def_done:
            return f'{indent}<file id="file-1"/>'
        file_def_done = True
        return (
            f'{indent}<file id="file-1">\n'
            f'{indent}  <name>source.mp4</name>\n'
            f'{indent}  <pathurl>{_pathurl(src)}</pathurl>\n'
            f'{indent}  <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>\n'
            f'{indent}  <duration>{src_frames}</duration>\n'
            f'{indent}  <media>\n'
            f'{indent}    <video><samplecharacteristics>'
            f'<width>{w}</width><height>{h}</height></samplecharacteristics></video>\n'
            f'{indent}    <audio><samplecharacteristics><depth>16</depth>'
            f'<samplerate>48000</samplerate></samplecharacteristics>'
            f'<channelcount>2</channelcount></audio>\n'
            f'{indent}  </media>\n'
            f'{indent}</file>'
        )

    v_items, a_items = [], []
    tl = 0  # posisi timeline (frame)
    for i, (s, e) in enumerate(segs):
        fin = int(round(s * fps))
        fout = int(round(e * fps))
        dur = fout - fin
        common = (
            f'      <duration>{src_frames}</duration>\n'
            f'      <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>\n'
            f'      <start>{tl}</start>\n'
            f'      <end>{tl + dur}</end>\n'
            f'      <in>{fin}</in>\n'
            f'      <out>{fout}</out>\n'
        )
        v_items.append(
            f'    <clipitem id="v{i + 1}">\n'
            f'      <name>{name} #{i + 1}</name>\n'
            f'      <enabled>TRUE</enabled>\n' + common +
            file_node("      ") + "\n"
            f'    </clipitem>'
        )
        a_items.append(
            f'    <clipitem id="a{i + 1}">\n'
            f'      <name>{name} audio #{i + 1}</name>\n'
            f'      <enabled>TRUE</enabled>\n' + common +
            f'      <file id="file-1"/>\n'
            f'      <sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>\n'
            f'    </clipitem>'
        )
        tl += dur

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE xmeml>\n'
        '<xmeml version="4">\n'
        '<sequence id="sequence-1">\n'
        f'  <name>{name}</name>\n'
        f'  <duration>{tl}</duration>\n'
        f'  <rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>\n'
        '  <media>\n'
        '  <video>\n'
        f'    <format><samplecharacteristics>'
        f'<width>{w}</width><height>{h}</height>'
        f'<rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>'
        f'</samplecharacteristics></format>\n'
        '    <track>\n' + "\n".join(v_items) + '\n    </track>\n'
        '  </video>\n'
        '  <audio>\n'
        '    <track>\n' + "\n".join(a_items) + '\n    </track>\n'
        '  </audio>\n'
        '  </media>\n'
        '</sequence>\n'
        '</xmeml>\n'
    )
