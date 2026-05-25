"""
Video Affiliate AI Generator — Flask Backend
Pipeline: Qwen VL Plus → Edge-TTS → FFmpeg
"""

import os
import sys
import json
import time
import uuid
import asyncio
import shutil
import subprocess
import re
import base64
from pathlib import Path

from flask import Flask, request, Response, send_from_directory, jsonify, stream_with_context
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ============================================================
# App Configuration
# ============================================================
app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config['MAX_CONTENT_LENGTH'] = 110 * 1024 * 1024  # 110 MB max upload

BASE_DIR = Path(__file__).parent.resolve()
UPLOAD_DIR = BASE_DIR / 'uploads'
OUTPUT_DIR = BASE_DIR / 'outputs'
TEMP_DIR = BASE_DIR / 'temp'
BACKSOUND_PATH = BASE_DIR / 'musik_backsound.mp3'

# Ensure directories exist
for d in [UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR]:
    d.mkdir(exist_ok=True)

# DashScope API configuration
DASHSCOPE_API_KEY = os.getenv('DASHSCOPE_API_KEY', '')
DASHSCOPE_BASE_URL = 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'

def build_qwen_prompt(duration_seconds: int) -> str:
    """Build a dynamic Qwen prompt based on actual video duration."""
    return (
        f"Kamu adalah copywriter video affiliate profesional. "
        f"Tonton video produk bisu ini (durasi {duration_seconds} detik). "
        f"Buatkan HANYA teks narasi jualan dalam Bahasa Indonesia yang pas untuk dibacakan selama {duration_seconds} detik. "
        f"\n\nATURAN KETAT:\n"
        f"1. Output HANYA berisi kalimat narasi yang siap dibacakan. JANGAN tulis analisis, deskripsi adegan, atau keterangan waktu.\n"
        f"2. DILARANG menggunakan tanda kurung siku [...], tanda kurung biasa (...), label 'Narator:', 'Detik ke-', atau format transkrip apapun.\n"
        f"3. DILARANG menulis komentar, catatan, atau penjelasan tambahan di luar narasi.\n"
        f"4. Narasi harus mengalir natural seperti orang bicara langsung ke kamera, penuh antusiasme dan kekaguman.\n"
        f"5. Sisipkan ekspresi WOW/kagum yang meledak di bagian paling menarik dari produk.\n"
        f"6. Akhiri dengan ajakan beli yang kuat (CTA).\n"
        f"\nLangsung tulis narasinya saja, tanpa pembuka atau penutup apapun:"
    )

# FFmpeg path — try to find it
FFMPEG_PATH = 'ffmpeg'
FFPROBE_PATH = 'ffprobe'


def find_ffmpeg():
    """Locate ffmpeg executable, checking common install paths on Windows."""
    global FFMPEG_PATH, FFPROBE_PATH
    
    # Method 1: shutil.which (checks current PATH)
    found = shutil.which('ffmpeg')
    if found:
        FFMPEG_PATH = found
        probe = shutil.which('ffprobe')
        FFPROBE_PATH = probe or 'ffprobe'
        print(f"[INFO] Found FFmpeg via PATH: {FFMPEG_PATH}")
        return

    # Method 2: Check common Windows install paths
    common_paths = [
        Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet' / 'Links',
        Path('C:/ffmpeg/bin'),
        Path('C:/Program Files/ffmpeg/bin'),
        Path(os.environ.get('USERPROFILE', '')) / 'scoop' / 'shims',
    ]
    
    # Method 3: Search WinGet packages directory for ffmpeg.exe
    winget_pkgs = Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet' / 'Packages'
    if winget_pkgs.exists():
        for ffmpeg_exe in winget_pkgs.rglob('ffmpeg.exe'):
            parent = ffmpeg_exe.parent
            common_paths.insert(0, parent)
            break

    for p in common_paths:
        ffmpeg_exe = p / 'ffmpeg.exe'
        if ffmpeg_exe.exists():
            FFMPEG_PATH = str(ffmpeg_exe)
            probe_exe = p / 'ffprobe.exe'
            FFPROBE_PATH = str(probe_exe) if probe_exe.exists() else 'ffprobe'
            print(f"[INFO] Found FFmpeg at: {FFMPEG_PATH}")
            return

    print("[WARNING] FFmpeg not found in PATH or common locations. Pipeline Step C will fail.")
    print("[WARNING] Try restarting your terminal or add FFmpeg to your PATH.")


find_ffmpeg()


# ============================================================
# Helper: Generate silent backsound if none exists
# ============================================================
def ensure_backsound():
    """Create a silent MP3 backsound placeholder if no file exists."""
    if BACKSOUND_PATH.exists():
        return str(BACKSOUND_PATH)
    
    silent_path = TEMP_DIR / 'silent_backsound.mp3'
    if silent_path.exists():
        return str(silent_path)
    
    try:
        # Generate 60s of silence using FFmpeg
        cmd = [
            FFMPEG_PATH, '-y',
            '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
            '-t', '60',
            '-c:a', 'libmp3lame', '-b:a', '32k',
            str(silent_path)
        ]
        subprocess.run(cmd, capture_output=True, timeout=15)
        if silent_path.exists():
            print("[INFO] Created silent backsound placeholder.")
            return str(silent_path)
    except Exception as e:
        print(f"[WARNING] Could not create silent backsound: {e}")
    
    return None


# ============================================================
# Helper: Get video duration using ffprobe
# ============================================================
def get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        cmd = [
            FFPROBE_PATH,
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            duration = float(result.stdout.strip())
            print(f"[ffprobe] Video duration: {duration:.1f}s")
            return duration
    except Exception as e:
        print(f"[ffprobe] Error getting duration: {e}")
    
    # Fallback: estimate from file size (rough: ~500KB per 10s for phone video)
    try:
        size_mb = Path(video_path).stat().st_size / (1024 * 1024)
        estimated = max(10, min(60, size_mb * 20))
        print(f"[ffprobe] Fallback estimated duration: {estimated:.0f}s")
        return estimated
    except Exception:
        return 30.0


# ============================================================
# Helper: Clean script text for TTS
# ============================================================
def clean_script_for_tts(text: str) -> str:
    """
    Regex-based cleaning to ensure only speakable text goes to Edge-TTS.
    Removes brackets, labels, timestamps, and formatting artifacts from Qwen output.
    """
    original_len = len(text)
    
    # Remove content in square brackets: [Detik 5-10], [Adegan Wow], etc.
    text = re.sub(r'\[.*?\]', '', text)
    
    # Remove content in parentheses: (menunjukkan produk), (suara antusias), etc.
    text = re.sub(r'\(.*?\)', '', text)
    
    # Remove content in curly braces: {music}, {sfx}, etc.
    text = re.sub(r'\{.*?\}', '', text)
    
    # Remove label prefixes: "Narator:", "Voice Over:", "VO:", "Narasi:", etc.
    text = re.sub(r'^\s*(Narator|Narrator|Voice\s*Over|VO|Narasi|Script|Teks)\s*:\s*', '', text, flags=re.MULTILINE | re.IGNORECASE)
    
    # Remove timestamp patterns: "Detik ke-5:", "00:05-00:10", "(5s)", "Detik 1-5:"
    text = re.sub(r'Detik\s*(ke[- ]?)?\d+[^:]*:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}\s*:?\s*', '', text)
    
    # Remove "Bagian X:" or "Scene X:" labels
    text = re.sub(r'^\s*(Bagian|Scene|Part|Segmen)\s*\d+\s*:\s*', '', text, flags=re.MULTILINE | re.IGNORECASE)
    
    # Remove markdown formatting: **, *, #, - at line starts
    text = re.sub(r'\*{1,2}(.*?)\*{1,2}', r'\1', text)
    text = re.sub(r'^\s*#{1,3}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-•]\s*', '', text, flags=re.MULTILINE)
    
    # Remove numbered list prefixes: "1.", "2.", etc.
    text = re.sub(r'^\s*\d+\.\s*', '', text, flags=re.MULTILINE)
    
    # Collapse multiple whitespace/newlines into single space
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    
    # Final trim
    text = text.strip()
    
    cleaned_len = len(text)
    if original_len != cleaned_len:
        print(f"[TTS Clean] Cleaned text: {original_len} -> {cleaned_len} chars ({original_len - cleaned_len} removed)")
    
    return text


# ============================================================
# Helper: SSE Event Formatting
# ============================================================
def sse_event(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ============================================================
# Step A: Qwen VL Plus — Video Understanding
# ============================================================
def step_a_video_understanding(video_path: str, duration_seconds: int = 30) -> str:
    """
    Send video to Qwen VL Plus for analysis and script generation.
    Uses base64 encoding for the video since DashScope intl doesn't 
    support local file paths directly.
    Duration is used to build a dynamic prompt matching the video length.
    """
    from openai import OpenAI

    if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY == 'your_api_key_here':
        raise ValueError(
            "API Key DashScope belum diset! "
            "Edit file .env dan masukkan DASHSCOPE_API_KEY yang valid."
        )

    client = OpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL
    )

    # Read and encode video to base64
    # DashScope has a 20MB data-uri limit. Base64 adds ~33% overhead.
    # So we need the raw video to be < ~14 MB. Compress if larger.
    video_path_obj = Path(video_path)
    file_size = video_path_obj.stat().st_size
    MAX_RAW_SIZE = 14 * 1024 * 1024  # 14 MB (safe margin for 20 MB base64 limit)
    
    source_path = video_path  # Path to encode (may be compressed copy)
    compressed_path = None
    
    if file_size > MAX_RAW_SIZE:
        print(f"[Step A] Video too large for API ({file_size / (1024*1024):.1f} MB > 14 MB limit)")
        print(f"[Step A] Compressing video for AI analysis...")
        compressed_path = str(Path(video_path).parent / f"_ai_temp_{Path(video_path).stem}.mp4")
        try:
            compress_cmd = [
                FFMPEG_PATH, '-y',
                '-i', video_path,
                '-vf', 'scale=-2:360',       # Scale down to 360p
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-crf', '35',                 # High compression
                '-an',                         # Remove audio (not needed for analysis)
                '-t', str(min(duration_seconds, 60)),  # Cap at 60s
                compressed_path
            ]
            result = subprocess.run(compress_cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and Path(compressed_path).exists():
                new_size = Path(compressed_path).stat().st_size
                print(f"[Step A] Compressed: {file_size/(1024*1024):.1f} MB -> {new_size/(1024*1024):.1f} MB")
                source_path = compressed_path
                file_size = new_size
            else:
                print(f"[Step A] Compression failed, using original. stderr: {result.stderr[-200:]}")
        except Exception as e:
            print(f"[Step A] Compression error: {e}, using original")
    
    print(f"[Step A] Encoding video ({file_size / 1024:.0f} KB) to base64...")
    
    with open(source_path, 'rb') as f:
        video_bytes = f.read()
    
    video_b64 = base64.b64encode(video_bytes).decode('utf-8')
    video_data_url = f"data:video/mp4;base64,{video_b64}"
    
    # Cleanup compressed temp file
    if compressed_path and Path(compressed_path).exists():
        try:
            Path(compressed_path).unlink()
        except Exception:
            pass

    print(f"[Step A] Sending to Qwen VL Plus (base64 size: {len(video_b64) / 1024:.0f} KB)...")

    response = client.chat.completions.create(
        model="qwen-vl-plus",
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "video_url",
                        "video_url": {"url": video_data_url}
                    },
                    {
                        "type": "text",
                        "text": build_qwen_prompt(duration_seconds)
                    }
                ]
            }
        ],
        max_tokens=1024
    )

    script = response.choices[0].message.content
    print(f"[Step A] Qwen response received ({len(script)} chars)")
    return script


# ============================================================
# Step B: Edge-TTS — Text to Speech
# ============================================================
def step_b_tts(
    text: str,
    voice: str,
    output_path: str,
    srt_path: Optional[str] = None,
    sub_font: str = "Arial",
    sub_size: int = 26,
    sub_color: str = "#FFFF00",
    sub_sec_color: str = "#FFFFFF",
    sub_opacity: float = 1.0,
):
    """Convert text to speech using Edge-TTS and optionally write SRT/ASS subtitles with custom styling."""
    import edge_tts

    print(f"[Step B] Generating TTS with voice: {voice}")
    
    async def _generate():
        if srt_path:
            communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")
            submaker = edge_tts.SubMaker()
            word_events = []
            with open(output_path, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        submaker.feed(chunk)
                        word_events.append(chunk)
            
            if srt_path.endswith('.ass'):
                generate_ass(
                    word_events, srt_path,
                    sub_font=sub_font, sub_size=sub_size,
                    sub_color=sub_color, sub_sec_color=sub_sec_color,
                    sub_opacity=sub_opacity
                )
            else:
                with open(srt_path, "w", encoding="utf-8") as f:
                    f.write(submaker.get_srt())
        else:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(output_path)

    # Run async in a new event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_generate())
    finally:
        loop.close()

    if not Path(output_path).exists():
        raise RuntimeError("Edge-TTS gagal menghasilkan file audio")
    
    audio_size = Path(output_path).stat().st_size
    print(f"[Step B] TTS audio saved: {output_path} ({audio_size / 1024:.0f} KB)")


# ============================================================
# Step B2: Generate SRT Subtitles (Audio-Driven)
# ============================================================
def get_audio_duration(audio_path: str) -> float:
    """Get exact audio duration in seconds using ffprobe."""
    try:
        cmd = [
            FFPROBE_PATH,
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            audio_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            duration = float(result.stdout.strip())
            print(f"[SRT] Audio duration: {duration:.3f}s")
            return duration
    except Exception as e:
        print(f"[SRT] Error getting audio duration: {e}")
    return 10.0  # fallback


def seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT time format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def split_text_to_sentences(text: str) -> list:
    """
    Split narration text into short subtitle segments.
    Splits on sentence endings (. ! ?) and also on commas for long sentences.
    Each segment should be comfortable to read (max ~60 chars).
    """
    # First split on sentence-ending punctuation
    raw_sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    
    segments = []
    for sentence in raw_sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        # If sentence is short enough, keep as-is
        if len(sentence) <= 60:
            segments.append(sentence)
        else:
            # Split long sentences on commas or semicolons
            parts = re.split(r'(?<=[,;])\s+', sentence)
            current = ''
            for part in parts:
                if current and len(current) + len(part) > 55:
                    segments.append(current.strip())
                    current = part
                else:
                    current = (current + ' ' + part).strip() if current else part
            if current:
                segments.append(current.strip())
    
    # Filter empty segments
    segments = [s for s in segments if s.strip()]
    
    if not segments:
        segments = [text.strip()]
    
    return segments


def generate_srt(
    narration_text: str,
    audio_duration: float,
    output_srt_path: str,
    sub_font: str = "Arial",
    sub_size: int = 26,
    sub_color: str = "#FFFF00",
    sub_sec_color: str = "#FFFFFF",
    sub_opacity: float = 1.0,
) -> str:
    """
    Generate SRT or ASS subtitle file with timing synchronized to TTS audio duration.
    """
    import os
    if os.path.exists(output_srt_path):
        print(f"[SRT] Subtitle file already exists (generated on the fly): {output_srt_path}")
        return output_srt_path
        
    segments = split_text_to_sentences(narration_text)
    total_chars = sum(len(s) for s in segments)
    
    if total_chars == 0:
        total_chars = 1  # prevent division by zero
    
    if output_srt_path.endswith('.ass'):
        # Generate styled fallback ASS content (non-karaoke but styled ASS!)
        ass_primary = convert_to_ass_color(sub_color, sub_opacity)
        ass_secondary = convert_to_ass_color(sub_sec_color, sub_opacity)
        
        ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{sub_font},{sub_size},{ass_primary},{ass_secondary},&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,2,10,10,280,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        current_time = 0.0
        for i, segment in enumerate(segments):
            char_ratio = len(segment) / total_chars
            segment_duration = max(0.8, audio_duration * char_ratio)
            
            start_time = current_time
            end_time = min(current_time + segment_duration, audio_duration)
            
            start_str = format_ass_time(start_time)
            end_str = format_ass_time(end_time)
            
            ass_content += f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{segment}\n"
            current_time = end_time
            
        with open(output_srt_path, 'w', encoding='utf-8') as f:
            f.write(ass_content)
            
        print(f"[SRT] ASS Subtitle fallback file saved: {output_srt_path}")
    else:
        print(f"[SRT] Generating {len(segments)} subtitle segments for {audio_duration:.2f}s audio")
        
        srt_content = ''
        current_time = 0.0
        
        for i, segment in enumerate(segments):
            # Calculate duration proportional to character count
            char_ratio = len(segment) / total_chars
            segment_duration = audio_duration * char_ratio
            
            # Minimum segment duration: 0.8 seconds
            segment_duration = max(0.8, segment_duration)
            
            start_time = current_time
            end_time = min(current_time + segment_duration, audio_duration)
            
            srt_content += f"{i + 1}\n"
            srt_content += f"{seconds_to_srt_time(start_time)} --> {seconds_to_srt_time(end_time)}\n"
            srt_content += f"{segment}\n\n"
            
            current_time = end_time
        
        # Write SRT file
        with open(output_srt_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)
        
        print(f"[SRT] Subtitle file saved: {output_srt_path}")
        print(f"[SRT] Preview first 3 segments:")
        for line in srt_content.split('\n')[:12]:
            if line.strip():
                print(f"       {line}")
        
    return output_srt_path


def format_ass_time(seconds: float) -> str:
    """Format seconds into ASS timestamp: H:MM:SS.cc"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centiseconds = int(round((seconds % 1) * 100))
    if centiseconds == 100:
        centiseconds = 99
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def convert_to_ass_color(hex_color: str, opacity: float) -> str:
    """
    Convert a standard hex color string (#RRGGBB or #RGB) and opacity (0.0 to 1.0)
    into ASS hexadecimal format: &HAABBGGRR.
    Note that ASS transparency is inverted (00 is fully opaque, FF is fully transparent).
    """
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join(c*2 for c in hex_color)
    if len(hex_color) != 6:
        hex_color = 'FFFFFF' # Fallback white
    
    r = hex_color[0:2]
    g = hex_color[2:4]
    b = hex_color[4:6]
    
    alpha_val = int(round((1.0 - opacity) * 255))
    alpha_val = max(0, min(255, alpha_val))
    a = f"{alpha_val:02X}"
    
    return f"&H{a}{b}{g}{r}"


def generate_ass(
    word_events,
    ass_path,
    sub_font: str = "Arial",
    sub_size: int = 26,
    sub_color: str = "#FFFF00",
    sub_sec_color: str = "#FFFFFF",
    sub_opacity: float = 1.0,
):
    """Generate ASS subtitle file with dynamic TikTok-style karaoke highlights and custom fonts/colors."""
    if not word_events:
        return
    
    # 1. Group word events into lines (max 4 words per line or on long pauses / punctuation)
    lines = []
    current_line = []
    
    for i, event in enumerate(word_events):
        text = event["text"]
        start = event["offset"] / 10000000.0
        duration = event["duration"] / 10000000.0
        end = start + duration
        
        word_info = {
            "text": text,
            "start": start,
            "end": end,
            "duration": duration
        }
        
        if not current_line:
            current_line.append(word_info)
        else:
            prev_word = current_line[-1]
            gap = start - prev_word["end"]
            
            is_punctuation = prev_word["text"][-1] in ('.', '!', '?', ',', ';') if prev_word["text"] else False
            is_long_pause = gap > 0.4
            is_max_words = len(current_line) >= 4
            
            if is_punctuation or is_long_pause or is_max_words:
                lines.append(current_line)
                current_line = [word_info]
            else:
                current_line.append(word_info)
                
    if current_line:
        lines.append(current_line)
        
    ass_primary = convert_to_ass_color(sub_color, sub_opacity)
    ass_secondary = convert_to_ass_color(sub_sec_color, sub_opacity)
    
    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 720
PlayResY: 1280

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{sub_font},{sub_size},{ass_primary},{ass_secondary},&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,2,10,10,280,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    for line in lines:
        line_start = line[0]["start"]
        line_end = line[-1]["end"]
        
        dialogue_text = ""
        for idx, w in enumerate(line):
            if idx > 0:
                gap = w["start"] - line[idx-1]["end"]
                if gap > 0.01:
                    gap_cs = int(round(gap * 100))
                    dialogue_text += f"{{\\kf{gap_cs}}}"
            
            w_duration_cs = int(round(w["duration"] * 100))
            dialogue_text += f"{{\\kf{w_duration_cs}}}{w['text']} "
            
        start_str = format_ass_time(line_start)
        end_str = format_ass_time(line_end)
        
        ass_content += f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{dialogue_text.strip()}\n"
        
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)


# ============================================================
# Step C: FFmpeg — Video Processing
# ============================================================
def step_c_ffmpeg(
    input_video: str,
    tts_audio: str,
    backsound: str,
    watermark_text: str,
    watermark_mode: str,
    watermark_logo: str,
    output_path: str,
    watermark_position: str = 'top-right',
    subtitle_path: str = None,
    sub_font: str = "Arial",
    sub_size: int = 26,
    sub_color: str = "#FFFF00",
    sub_sec_color: str = "#FFFFFF",
    sub_opacity: float = 1.0,
    wm_opacity: float = 0.65,
):
    """
    Process video with FFmpeg:
    - Anti-copyright: blurred background + centered scaled video
    - Text watermark (drawtext) or PNG logo overlay with flexible opacity
    - Backsound mixing at 12% volume + TTS narration
    - Dynamic ASS/SRT subtitles styled dynamically
    """
    print(f"[Step C] Building FFmpeg command...")

    # Determine font path for Windows
    font_path = "C\\\\:/Windows/Fonts/arial.ttf"
    
    # Escape watermark text for FFmpeg drawtext filter
    safe_text = (watermark_text or 'Watermark')
    # Escape special FFmpeg characters
    for ch in ["'", ":", "\\", "%"]:
        safe_text = safe_text.replace(ch, f"\\{ch}")

    # Check if backsound is available
    has_backsound = backsound and Path(backsound).exists()

    # Map watermark position to FFmpeg x:y coordinates
    position_map = {
        'top-right':     ('x=W-tw-30', 'y=30'),
        'top-left':      ('x=30', 'y=30'),
        'top-center':    ('x=(W-tw)/2', 'y=30'),
        'center':        ('x=(W-tw)/2', 'y=(H-th)/2'),
        'bottom-right':  ('x=W-tw-30', 'y=H-th-30'),
        'bottom-left':   ('x=30', 'y=H-th-30'),
        'bottom-center': ('x=(W-tw)/2', 'y=H-th-30'),
    }

    # Random mode: cycle through 6 center-friendly positions every 5 seconds (30s cycle)
    if watermark_position == 'random':
        # 1. Center, 2. Lower-Middle, 3. Upper-Middle, 4. Middle-Right, 5. Middle-Left, 6. Center
        wm_x = "x='if(lt(mod(t\\,30)\\,5)\\,(W-tw)/2\\,if(lt(mod(t\\,30)\\,10)\\,(W-tw)/2\\,if(lt(mod(t\\,30)\\,15)\\,(W-tw)/2\\,if(lt(mod(t\\,30)\\,20)\\,W-tw-100\\,if(lt(mod(t\\,30)\\,25)\\,100\\,(W-tw)/2)))))'"
        wm_y = "y='if(lt(mod(t\\,30)\\,5)\\,(H-th)/2\\,if(lt(mod(t\\,30)\\,10)\\,H-th-200\\,if(lt(mod(t\\,30)\\,15)\\,200\\,if(lt(mod(t\\,30)\\,20)\\,(H-th)/2\\,if(lt(mod(t\\,30)\\,25)\\,(H-th)/2\\,(H-th)/2)))))'"
        logo_x = "x='if(lt(mod(t\\,30)\\,5)\\,(W-w)/2\\,if(lt(mod(t\\,30)\\,10)\\,(W-w)/2\\,if(lt(mod(t\\,30)\\,15)\\,(W-w)/2\\,if(lt(mod(t\\,30)\\,20)\\,W-w-100\\,if(lt(mod(t\\,30)\\,25)\\,100\\,(W-w)/2)))))'"
        logo_y = "y='if(lt(mod(t\\,30)\\,5)\\,(H-h)/2\\,if(lt(mod(t\\,30)\\,10)\\,H-h-200\\,if(lt(mod(t\\,30)\\,15)\\,200\\,if(lt(mod(t\\,30)\\,20)\\,(H-h)/2\\,if(lt(mod(t\\,30)\\,25)\\,(H-h)/2\\,(H-h)/2)))))'"
    else:
        pos = position_map.get(watermark_position, position_map['top-right'])
        wm_x = pos[0]
        wm_y = pos[1]
        logo_x, logo_y = "x=W-w-30", "y=30"

    print(f"[Step C] Watermark position: {watermark_position} -> {wm_x}, {wm_y}")

    # Build dynamic inputs and trace their indices
    input_args = ['-i', input_video, '-i', tts_audio]
    next_index = 2
    
    backsound_index = -1
    if has_backsound:
        input_args += ['-i', backsound]
        backsound_index = next_index
        next_index += 1
        
    logo_index = -1
    has_logo = watermark_logo and Path(watermark_logo).exists()
    if has_logo:
        input_args += ['-i', watermark_logo]
        logo_index = next_index
        next_index += 1

    # Base background and video blending filter
    filter_complex = (
        f"[0:v]scale=720:1280,boxblur=20:5[bg];"
        f"[0:v]scale=640:1136[main];"
        f"[bg][main]overlay=(W-w)/2:(H-h)/2[vid_with_bg]"
    )
    last_vid = "[vid_with_bg]"
    
    # 1. Overlay logo watermark if present
    if logo_index != -1:
        logo_out = "[vid_logo]" if watermark_text or (subtitle_path and Path(subtitle_path).exists()) else "[vid_final]"
        filter_complex += (
            f";[{logo_index}:v]scale=100:-1,format=rgba,colorchannelmixer=aa={wm_opacity}[logo];"
            f"{last_vid}[logo]overlay={logo_x}:{logo_y}{logo_out}"
        )
        last_vid = logo_out
        
    # 2. Draw text watermark if present
    if watermark_text:
        text_out = "[vid_text]" if (subtitle_path and Path(subtitle_path).exists()) else "[vid_final]"
        filter_complex += (
            f";{last_vid}drawtext=text='{safe_text}':"
            f"fontfile={font_path}:"
            f"fontsize=24:fontcolor=white@{wm_opacity}:"
            f"{wm_x}:{wm_y}{text_out}"
        )
        last_vid = text_out
        
    # 3. Add subtitles if present
    if subtitle_path and Path(subtitle_path).exists():
        srt_escaped = subtitle_path.replace('\\', '/').replace(':', '\\:')
        if subtitle_path.endswith('.ass'):
            filter_complex += (
                f";{last_vid}subtitles='{srt_escaped}'[vid_final]"
            )
        else:
            ass_primary = convert_to_ass_color(sub_color, sub_opacity)
            ass_secondary = convert_to_ass_color(sub_sec_color, sub_opacity)
            filter_complex += (
                f";{last_vid}subtitles='{srt_escaped}':"
                f"force_style='FontName={sub_font},FontSize={sub_size},"
                f"PrimaryColour={ass_primary},SecondaryColour={ass_secondary},"
                f"OutlineColour=&H00000000,Outline=2,Shadow=0,Alignment=2,"
                f"MarginV=280'[vid_final]"
            )
            
    # If neither logo, text watermark, nor subtitles are present, route the blended bg directly
    if last_vid == "[vid_with_bg]":
        filter_complex = filter_complex.replace("[vid_with_bg]", "[vid_final]")

    # Audio mixing mapping using the tracked backsound index
    if backsound_index != -1:
        filter_complex += (
            f";[{backsound_index}:a]volume=0.12[bg_audio];"
            f"[1:a][bg_audio]amix=inputs=2:duration=first[aud_final]"
        )
        audio_map = '[aud_final]'
    else:
        audio_map = '1:a'

    # Full FFmpeg command
    cmd = [
        FFMPEG_PATH, '-y',
        *input_args,
        '-filter_complex', filter_complex,
        '-map', '[vid_final]',
        '-map', audio_map,
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '23',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-shortest',
        '-movflags', '+faststart',
        output_path
    ]

    print(f"[Step C] Running FFmpeg...")
    print(f"[Step C] Command: {' '.join(cmd)}")

    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300  # 5 minute timeout
    )

    if process.returncode != 0:
        stderr_last = process.stderr[-1500:] if process.stderr else 'No stderr'
        print(f"[Step C] FFmpeg FAILED:\n{stderr_last}")
        raise RuntimeError(f"FFmpeg gagal merender video. Error: {stderr_last[-300:]}")

    if not Path(output_path).exists():
        raise RuntimeError("FFmpeg selesai tapi file output tidak ditemukan")
    
    output_size = Path(output_path).stat().st_size
    print(f"[Step C] Video output saved: {output_path} ({output_size / (1024*1024):.1f} MB)")


# ============================================================
# Routes
# ============================================================
@app.route('/')
def index():
    """Serve the main frontend page."""
    return send_from_directory('static', 'index.html')


@app.route('/outputs/<path:filename>')
def serve_output(filename):
    """Serve processed output videos with Range request support for streaming."""
    from flask import send_file
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        return jsonify({'error': 'File not found'}), 404
    return send_file(
        str(file_path),
        mimetype='video/mp4',
        conditional=True  # Enables Range requests for video seeking/streaming
    )


@app.route('/api/generate', methods=['POST'])
def generate():
    """
    Main pipeline endpoint.
    Receives video + settings, runs 3-step AI pipeline,
    streams progress via SSE, returns final result.
    """
    
    def pipeline_stream():
        job_id = str(uuid.uuid4())[:8]
        tts_path = None
        output_path = None

        try:
            # --- Receive and validate inputs ---
            video_file = request.files.get('video')
            if not video_file:
                yield sse_event({'step': 'error', 'message': 'Tidak ada file video yang diunggah'})
                return

            voice = request.form.get('voice', 'id-ID-GadisNeural')
            wm_mode = request.form.get('watermark_mode', 'text')
            wm_text = request.form.get('watermark_text', '')
            wm_position = request.form.get('watermark_position', 'top-right')

            # Dynamic subtitle styling and watermark opacity inputs
            sub_font = request.form.get('sub_font', 'Arial')
            sub_size = int(request.form.get('sub_size', 26))
            sub_color = request.form.get('sub_color', '#FFFF00')
            sub_sec_color = request.form.get('sub_sec_color', '#FFFFFF')
            sub_opacity = float(request.form.get('sub_opacity', 1.0))
            wm_opacity = float(request.form.get('wm_opacity', 0.65))
            
            # Save uploaded video
            video_ext = Path(video_file.filename).suffix or '.mp4'
            video_filename = f"{job_id}_input{video_ext}"
            video_path = str(UPLOAD_DIR / video_filename)
            video_file.save(video_path)
            print(f"\n{'='*60}")
            print(f"[Pipeline] Job {job_id} started")
            print(f"[Pipeline] Video: {video_filename} ({Path(video_path).stat().st_size / 1024:.0f} KB)")
            print(f"[Pipeline] Voice: {voice}")
            print(f"[Pipeline] Watermark: {wm_mode} = '{wm_text}'")
            print(f"{'='*60}")

            # Save logo if uploaded
            logo_path = None
            logo_file = request.files.get('watermark_logo')
            if logo_file:
                logo_filename = f"{job_id}_logo.png"
                logo_path = str(UPLOAD_DIR / logo_filename)
                logo_file.save(logo_path)

            # --- Get video duration via ffprobe ---
            video_duration = get_video_duration(video_path)
            video_duration_int = max(5, int(round(video_duration)))
            print(f"[Pipeline] Video duration: {video_duration_int}s")

            # --- Step A: Qwen VL Plus ---
            yield sse_event({'step': 'A_start', 'status': 'processing'})
            
            try:
                script = step_a_video_understanding(video_path, duration_seconds=video_duration_int)
            except Exception as e:
                print(f"[Step A] Error: {e}")
                # Fallback: generate a generic script for PoC testing
                script = (
                    "Hai semuanya! Kamu harus lihat produk keren ini! "
                    "Lihat betapa luar biasanya kualitas produk ini, benar-benar amazing! "
                    "Wow, coba perhatikan bagian ini — luar biasa kan?! "
                    "Tidak heran produk ini sudah viral di mana-mana! "
                    "Buruan grab sebelum kehabisan, link ada di bio ya! "
                    f"\n\n[⚠️ Catatan: Script fallback digunakan karena Qwen API error: {str(e)[:100]}]"
                )
                print(f"[Step A] Using fallback script due to error")

            yield sse_event({
                'step': 'A_done', 
                'status': 'done',
                'script_preview': script[:150] + '...' if len(script) > 150 else script
            })

            # --- Step B: Edge-TTS ---
            yield sse_event({'step': 'B_start', 'status': 'processing'})

            # Clean script for TTS using regex cleaner
            tts_text = clean_script_for_tts(script)
            
            # Also strip fallback warning markers if present
            if '[' in tts_text and 'Catatan' in tts_text:
                tts_text = re.sub(r'\[.*?Catatan.*?\]', '', tts_text).strip()
            
            if not tts_text:
                tts_text = "Hai semuanya! Produk ini luar biasa, buruan cek sekarang!"
                print("[Step B] WARNING: Cleaned text was empty, using minimal fallback")

            tts_filename = f"{job_id}_narasi.mp3"
            tts_path = str(TEMP_DIR / tts_filename)
            srt_filename = f"{job_id}_subtitles.ass"
            srt_path = str(TEMP_DIR / srt_filename)

            try:
                step_b_tts(
                    tts_text, voice, tts_path, srt_path=srt_path,
                    sub_font=sub_font, sub_size=sub_size, sub_color=sub_color,
                    sub_sec_color=sub_sec_color, sub_opacity=sub_opacity
                )
            except Exception as e:
                print(f"[Step B] Error: {e}")
                raise RuntimeError(f"Text-to-Speech gagal: {str(e)}")

            yield sse_event({'step': 'B_done', 'status': 'done'})

            # --- Generate SRT Subtitles (audio-driven timing) ---
            tts_audio_duration = get_audio_duration(tts_path)
            try:
                generate_srt(
                    tts_text, tts_audio_duration, srt_path,
                    sub_font=sub_font, sub_size=sub_size, sub_color=sub_color,
                    sub_sec_color=sub_sec_color, sub_opacity=sub_opacity
                )
            except Exception as e:
                print(f"[SRT] Error generating subtitles: {e}")
                srt_path = None  # Continue without subtitles

            # --- Step C: FFmpeg ---
            yield sse_event({'step': 'C_start', 'status': 'processing'})

            backsound = ensure_backsound()
            
            output_filename = f"{job_id}_output_final.mp4"
            output_path = str(OUTPUT_DIR / output_filename)

            try:
                step_c_ffmpeg(
                    input_video=video_path,
                    tts_audio=tts_path,
                    backsound=backsound,
                    watermark_text=wm_text,
                    watermark_mode=wm_mode,
                    watermark_logo=logo_path,
                    output_path=output_path,
                    watermark_position=wm_position,
                    subtitle_path=srt_path,
                    sub_font=sub_font,
                    sub_size=sub_size,
                    sub_color=sub_color,
                    sub_sec_color=sub_sec_color,
                    sub_opacity=sub_opacity,
                    wm_opacity=wm_opacity,
                )
            except Exception as e:
                print(f"[Step C] Error: {e}")
                raise RuntimeError(f"Fengine rendering gagal: {str(e)[:200]}")

            yield sse_event({'step': 'C_done', 'status': 'done'})

            # --- Complete ---
            yield sse_event({
                'step': 'complete',
                'status': 'complete',
                'video_url': f'/outputs/{output_filename}',
                'filename': output_filename,
                'caption': script
            })

            print(f"\n[Pipeline] Job {job_id} COMPLETED SUCCESSFULLY ✓")

        except Exception as e:
            print(f"\n[Pipeline] Job {job_id} FAILED: {e}")
            yield sse_event({
                'step': 'error',
                'status': 'error',
                'message': str(e)
            })

        finally:
            # Cleanup temp files (keep output)
            try:
                if tts_path and Path(tts_path).exists():
                    Path(tts_path).unlink()
            except Exception:
                pass

    return Response(
        stream_with_context(pipeline_stream()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )


# ============================================================
# Error Handlers
# ============================================================
@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'File terlalu besar! Maksimal 15 MB.'}), 413


@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Internal server error'}), 500


# ============================================================
# Main
# ============================================================
if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  Video Affiliate AI Generator — PoC Server")
    print("=" * 60)
    print(f"  FFmpeg:     {FFMPEG_PATH}")
    api_ok = DASHSCOPE_API_KEY and DASHSCOPE_API_KEY != 'your_api_key_here'
    print(f"  API Key:    {'[OK] Set' if api_ok else '[!!] NOT SET (edit .env)'}")
    print(f"  Backsound:  {'[OK] Found' if BACKSOUND_PATH.exists() else '[--] Not found (will use silence)'}")
    print(f"  Uploads:    {UPLOAD_DIR}")
    print(f"  Outputs:    {OUTPUT_DIR}")
    print("=" * 60)
    print("  Starting server at http://localhost:5000")
    print("=" * 60 + "\n")
    
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=True,
        threaded=True
    )
