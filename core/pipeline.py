"""
Core Engine Pipeline Module.
Contains all logic for video/audio understanding, subtitle generation, and FFmpeg processing.
"""

import os
import re
import shutil
import subprocess
import asyncio
import base64
from pathlib import Path
from openai import OpenAI
from typing import Optional

import redis
from core.config import get_settings
settings = get_settings()

def register_task_pid(job_id: str, pid: int):
    """Register running FFmpeg process PID in Redis for cancellation support."""
    try:
        r = redis.from_url(settings.REDIS_URL)
        r.setex(f"task_pid:{job_id}", 3600, str(pid))
        r.close()
        print(f"[PID REG] Registered PID {pid} for job {job_id}")
    except Exception as e:
        print(f"[PID REG ERROR] Error: {e}")

def unregister_task_pid(job_id: str):
    """Remove FFmpeg process PID from Redis when finished."""
    try:
        r = redis.from_url(settings.REDIS_URL)
        r.delete(f"task_pid:{job_id}")
        r.close()
        print(f"[PID UNREG] Unregistered PID for job {job_id}")
    except Exception as e:
        print(f"[PID UNREG ERROR] Error: {e}")

# ============================================================
# Directory Configuration
# ============================================================
BASE_DIR = Path(__file__).parent.parent.resolve()
UPLOAD_DIR = BASE_DIR / 'uploads'
OUTPUT_DIR = BASE_DIR / 'outputs'
TEMP_DIR = BASE_DIR / 'temp'
BACKSOUND_PATH = BASE_DIR / 'musik_backsound.mp3'

BACKSOUNDS_DIR = BASE_DIR / 'backsounds'

# Ensure directories exist
for d in [UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR, BACKSOUNDS_DIR]:
    d.mkdir(exist_ok=True)

def init_backsound_placeholders(ffmpeg_path, backsounds_dir):
    backsounds_dir = Path(backsounds_dir)
    for i in range(1, 4):
        p = backsounds_dir / f"backsound{i}.mp3"
        if not p.exists() or p.stat().st_size == 0:
            try:
                # Generate 60s of silence
                cmd = [
                    ffmpeg_path, '-y',
                    '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
                    '-t', '60',
                    '-c:a', 'libmp3lame', '-b:a', '32k',
                    str(p)
                ]
                res = subprocess.run(cmd, capture_output=True, timeout=10)
                if res.returncode != 0 or not p.exists() or p.stat().st_size == 0:
                    if p.exists():
                        try:
                            p.unlink()
                        except Exception:
                            pass
                    print(f"[WARNING] Failed to generate placeholder {p} (FFmpeg returned {res.returncode})")
                else:
                    print(f"[INFO] Created silent placeholder: {p}")
            except Exception as e:
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass
                print(f"[WARNING] Could not create placeholder {p}: {e}")

# DashScope / Aliyun API Key
from dotenv import load_dotenv
load_dotenv(dotenv_path=BASE_DIR / ".env")
DASHSCOPE_API_KEY = os.getenv('DASHSCOPE_API_KEY', '')
DASHSCOPE_BASE_URL = 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'

# ============================================================
# FFmpeg / ffprobe Path Resolution
# ============================================================
FFMPEG_PATH = 'ffmpeg'
FFPROBE_PATH = 'ffprobe'

def find_ffmpeg():
    """Locate ffmpeg and ffprobe executables on the system."""
    global FFMPEG_PATH, FFPROBE_PATH
    
    # Method 1: Check PATH
    found = shutil.which('ffmpeg')
    if found:
        FFMPEG_PATH = found
        probe = shutil.which('ffprobe')
        FFPROBE_PATH = probe or 'ffprobe'
        return FFMPEG_PATH, FFPROBE_PATH

    # Method 2: Common Windows install paths
    common_paths = [
        Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet' / 'Links',
        Path('C:/ffmpeg/bin'),
        Path('C:/Program Files/ffmpeg/bin'),
        Path(os.environ.get('USERPROFILE', '')) / 'scoop' / 'shims',
    ]
    
    # Method 3: Search WinGet Packages
    winget_pkgs = Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'WinGet' / 'Packages'
    if winget_pkgs.exists():
        for ffmpeg_exe in winget_pkgs.rglob('ffmpeg.exe'):
            common_paths.insert(0, ffmpeg_exe.parent)
            break

    for p in common_paths:
        ffmpeg_exe = p / 'ffmpeg.exe'
        if ffmpeg_exe.exists():
            FFMPEG_PATH = str(ffmpeg_exe)
            probe_exe = p / 'ffprobe.exe'
            FFPROBE_PATH = str(probe_exe) if probe_exe.exists() else 'ffprobe'
            break

    return FFMPEG_PATH, FFPROBE_PATH

# Initialize paths
find_ffmpeg()

# Run placeholder initialization
init_backsound_placeholders(FFMPEG_PATH, BACKSOUNDS_DIR)

# ============================================================
# Pipeline Core Logic Functions
# ============================================================

def build_qwen_prompt(duration_seconds: int) -> str:
    """Build a dynamic Qwen prompt based on actual video duration."""
    return (
        f"Kamu adalah copywriter video affiliate profesional. "
        f"Tonton video produk bisu ini (durasi {duration_seconds} detik). "
        f"Buat dan susun output dalam format persis seperti di bawah ini:\n\n"
        f"[JUDUL]\nTulis 1 baris Judul Video yang sangat menarik perhatian (headline hook).\n\n"
        f"[HASHTAG]\nTulis beberapa hashtag viral yang relevan dengan produk (misal: #produkviral #racunshopee).\n\n"
        f"[NARASI]\nTeks narasi jualan penuh antusiasme dalam Bahasa Indonesia yang pas dibacakan selama {duration_seconds} detik.\n\n"
        f"ATURAN KETAT UNTUK [NARASI]:\n"
        f"1. Output HANYA berisi kalimat narasi yang siap dibacakan. JANGAN tulis analisis, deskripsi adegan, atau keterangan waktu.\n"
        f"2. DILARANG menggunakan tanda kurung siku [...], tanda kurung biasa (...), label 'Narator:', 'Detik ke-', atau format transkrip apapun di bagian [NARASI].\n"
        f"3. Narasi harus mengalir natural seperti orang bicara langsung ke kamera.\n"
        f"4. Sisipkan ekspresi WOW/kagum di bagian paling menarik dari produk.\n"
        f"5. Akhiri narasi dengan ajakan beli yang kuat (CTA).\n"
        f"\nIngat, langsung tulis dengan pembungkus tag [JUDUL], [HASHTAG], dan [NARASI]:"
    )

def ensure_backsound() -> str:
    """Create a silent MP3 backsound placeholder if no file exists."""
    if BACKSOUND_PATH.exists() and BACKSOUND_PATH.stat().st_size > 0:
        return str(BACKSOUND_PATH)
    
    silent_path = TEMP_DIR / 'silent_backsound.mp3'
    if silent_path.exists() and silent_path.stat().st_size > 0:
        return str(silent_path)
    
    try:
        cmd = [
            FFMPEG_PATH, '-y',
            '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
            '-t', '60',
            '-c:a', 'libmp3lame', '-b:a', '32k',
            str(silent_path)
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=15)
        if res.returncode != 0 or not silent_path.exists() or silent_path.stat().st_size == 0:
            if silent_path.exists():
                try:
                    silent_path.unlink()
                except Exception:
                    pass
        else:
            return str(silent_path)
    except Exception as e:
        if silent_path.exists():
            try:
                silent_path.unlink()
            except Exception:
                pass
        print(f"[WARNING] Could not create silent backsound: {e}")
    
    return None

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
            return float(result.stdout.strip())
    except Exception as e:
        print(f"[ffprobe] Error getting duration: {e}")
    
    # Fallback based on file size
    try:
        size_mb = Path(video_path).stat().st_size / (1024 * 1024)
        return max(10.0, min(60.0, size_mb * 20.0))
    except Exception:
        return 30.0

def clean_script_for_tts(text: str) -> str:
    """Regex-based cleaning to ensure only speakable text goes to Edge-TTS."""
    original_len = len(text)
    
    # Remove bracketed content
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\(.*?\)', '', text)
    text = re.sub(r'\{.*?\}', '', text)
    
    # Remove label prefixes
    text = re.sub(r'^\s*(Narator|Narrator|Voice\s*Over|VO|Narasi|Script|Teks)\s*:\s*', '', text, flags=re.MULTILINE | re.IGNORECASE)
    
    # Remove timestamp patterns
    text = re.sub(r'Detik\s*(ke[- ]?)?\d+[^:]*:\s*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}\s*:?\s*', '', text)
    text = re.sub(r'^\s*(Bagian|Scene|Part|Segmen)\s*\d+\s*:\s*', '', text, flags=re.MULTILINE | re.IGNORECASE)
    
    # Remove formatting markdown
    text = re.sub(r'\*{1,2}(.*?)\*{1,2}', r'\1', text)
    text = re.sub(r'^\s*#{1,3}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-•]\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s*', '', text, flags=re.MULTILINE)
    
    # Collapse multiple whitespaces
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)
    
    text = text.strip()
    return text

def step_a_video_understanding(video_path: str, duration_seconds: int = 30) -> str:
    """Send video to Qwen VL Plus for analysis and script generation."""
    if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY == 'your_api_key_here':
        raise ValueError("DASHSCOPE_API_KEY is not set or invalid in environment variables.")

    client = OpenAI(
        api_key=DASHSCOPE_API_KEY,
        base_url=DASHSCOPE_BASE_URL
    )

    video_path_obj = Path(video_path)
    file_size = video_path_obj.stat().st_size
    MAX_RAW_SIZE = 14 * 1024 * 1024  # 14 MB safe limit for 20MB Base64 API limit
    
    source_path = video_path
    compressed_path = None
    
    if file_size > MAX_RAW_SIZE:
        compressed_path = str(Path(video_path).parent / f"_ai_temp_{Path(video_path).stem}.mp4")
        try:
            compress_cmd = [
                FFMPEG_PATH, '-y',
                '-i', video_path,
                '-vf', 'scale=-2:360',
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-crf', '35',
                '-an',
                '-t', str(min(duration_seconds, 60)),
                compressed_path
            ]
            result = subprocess.run(compress_cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and Path(compressed_path).exists():
                source_path = compressed_path
        except Exception as e:
            print(f"[Step A] Compression error: {e}")
    
    with open(source_path, 'rb') as f:
        video_bytes = f.read()
    
    video_b64 = base64.b64encode(video_bytes).decode('utf-8')
    video_data_url = f"data:video/mp4;base64,{video_b64}"
    
    if compressed_path and Path(compressed_path).exists():
        try:
            Path(compressed_path).unlink()
        except Exception:
            pass

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
        max_tokens=1024,
        timeout=120.0
    )

    return response.choices[0].message.content

async def step_b_tts(
    text: str,
    voice: str,
    output_path: str,
    srt_path: Optional[str] = None,
    sub_font: str = "Arial",
    sub_size: int = 40,
    sub_color: str = "#00FF4C",
    sub_sec_color: str = "#FFFFFF",
    sub_opacity: float = 1.0,
):
    """Convert text to speech using Edge-TTS asynchronously and optionally write SRT/ASS subtitles with dynamic custom styling."""
    # Check for local/alternative TTS engines
    if voice.startswith("gtts-id"):
        from core.tts_local import generate_gtts
        await generate_gtts(text, output_path)
        return
        
    if voice.startswith("piper"):
        from core.tts_local import generate_piper
        await generate_piper(text, output_path, FFMPEG_PATH)
        return
        
    if voice.startswith("supertonic"):
        from core.tts_local import generate_supertonic
        await generate_supertonic(text, output_path, voice)
        return
        

    # --- Edge-TTS with timeout protection ---
    import edge_tts
    
    async def _run_edge_tts():
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
    
    # Try Edge-TTS with 60s timeout to prevent infinite hang on blocked VPS IPs
    try:
        await asyncio.wait_for(_run_edge_tts(), timeout=60)
        if Path(output_path).exists() and Path(output_path).stat().st_size > 0:
            return
        raise RuntimeError("Edge-TTS produced empty file")
    except (asyncio.TimeoutError, Exception) as e:
        print(f"[Step B WARNING] Edge-TTS failed/timed out ({voice}): {e}")
        # Clean up partial/empty files
        for p in [output_path, srt_path]:
            if p and Path(p).exists():
                try:
                    Path(p).unlink()
                except Exception:
                    pass

    # Fallback 1: Piper-TTS (100% local, no internet needed)
    try:
        print(f"[Step B FALLBACK 1] Trying Piper-TTS (local/offline)...")
        from core.tts_local import generate_piper
        await generate_piper(text, output_path, FFMPEG_PATH)
        if Path(output_path).exists() and Path(output_path).stat().st_size > 0:
            print(f"[Step B FALLBACK 1] Piper-TTS completed successfully.")
            return
    except Exception as e2:
        print(f"[Step B FALLBACK 1 FAILED] Piper-TTS failed: {e2}")
        if Path(output_path).exists():
            try:
                Path(output_path).unlink()
            except Exception:
                pass

    # Fallback 2: gTTS (Google Translate — very reliable on VPS)
    try:
        print(f"[Step B FALLBACK 2] Trying gTTS (Google Translate)...")
        from core.tts_local import generate_gtts
        await generate_gtts(text, output_path)
        if Path(output_path).exists() and Path(output_path).stat().st_size > 0:
            print(f"[Step B FALLBACK 2] gTTS completed successfully.")
            return
    except Exception as e3:
        print(f"[Step B FALLBACK 2 FAILED] gTTS failed: {e3}")

    raise RuntimeError("Semua mesin TTS gagal (Edge-TTS, Piper, gTTS). Periksa koneksi server.")


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
            return float(result.stdout.strip())
    except Exception as e:
        print(f"[SRT] Error getting audio duration: {e}")
    return 10.0

def seconds_to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT time format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def split_text_to_sentences(text: str) -> list:
    """Split narration text into short readable subtitle segments."""
    raw_sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    segments = []
    
    for sentence in raw_sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= 60:
            segments.append(sentence)
        else:
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
    
    segments = [s for s in segments if s.strip()]
    if not segments:
        segments = [text.strip()]
    return segments

def generate_srt(
    narration_text: str,
    audio_duration: float,
    output_srt_path: str,
    sub_font: str = "Arial",
    sub_size: int = 40,
    sub_color: str = "#00FF4C",
    sub_sec_color: str = "#FFFFFF",
    sub_opacity: float = 1.0,
) -> str:
    """Generate SRT or ASS subtitle file synchronized to the audio duration with dynamic custom styling."""
    import os
    if os.path.exists(output_srt_path):
        return output_srt_path
        
    segments = split_text_to_sentences(narration_text)
    total_chars = sum(len(s) for s in segments)
    if total_chars == 0:
        total_chars = 1
        
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
            
            # Dynamic karaoke timing approximation for local / non-EdgeTTS voices
            words = segment.split()
            if words:
                seg_chars = sum(len(w) for w in words)
                if seg_chars == 0:
                    seg_chars = 1
                seg_dur = end_time - start_time
                dialogue_text = ""
                for idx, w in enumerate(words):
                    w_dur = seg_dur * (len(w) / seg_chars)
                    w_dur_cs = max(1, int(round(w_dur * 100)))
                    dialogue_text += f"{{\\kf{w_dur_cs}}}{w} "
                dialogue_text = dialogue_text.strip()
            else:
                dialogue_text = segment
                
            ass_content += f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{dialogue_text}\n"
            current_time = end_time
            
        with open(output_srt_path, 'w', encoding='utf-8') as f:
            f.write(ass_content)
    else:
        # Standard SRT fallback
        srt_content = ''
        current_time = 0.0
        
        for i, segment in enumerate(segments):
            char_ratio = len(segment) / total_chars
            segment_duration = max(0.8, audio_duration * char_ratio)
            
            start_time = current_time
            end_time = min(current_time + segment_duration, audio_duration)
            
            srt_content += f"{i + 1}\n"
            srt_content += f"{seconds_to_srt_time(start_time)} --> {seconds_to_srt_time(end_time)}\n"
            srt_content += f"{segment}\n\n"
            
            current_time = end_time
            
        with open(output_srt_path, 'w', encoding='utf-8') as f:
            f.write(srt_content)
        
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
    sub_size: int = 40,
    sub_color: str = "#00FF4C",
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

def step_c_ffmpeg(
    input_video: str,
    tts_audio: str,
    backsound: Optional[str],
    watermark_text: str,
    watermark_mode: str,
    watermark_logo: Optional[str],
    output_path: str,
    watermark_position: str = 'top-right',
    subtitle_path: Optional[str] = None,
    job_id: Optional[str] = None,
    sub_font: str = "Arial",
    sub_size: int = 40,
    sub_color: str = "#00FF4C",
    sub_sec_color: str = "#FFFFFF",
    sub_opacity: float = 1.0,
    wm_opacity: float = 0.65,
    use_speed_ramping: str = "true",
    use_camera_shake: str = "false",
    thumbnail_path: Optional[str] = None,
    backsound_volume: float = 0.12,
):
    """Process video using FFmpeg with anti-copyright, customized subtitles, adjustable watermark opacity, and optional thumbnail cover."""
    # Get original input video duration
    video_dur = get_video_duration(input_video)
    
    # Calculate a safe duration target slightly shorter than the input video (by 50ms)
    # to prevent any possibility of frame exhaustion hangs
    t_arg = f"{max(1.0, video_dur - 0.05):.3f}"

    # Dynamic font path for Windows vs Linux (aaPanel)
    if os.name == 'nt':
        font_path = "C\\\\:/Windows/Fonts/arial.ttf"
    else:
        # Check standard Linux paths
        linux_fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/LiberationSans-Regular.ttf"
        ]
        font_path = None
        for p in linux_fonts:
            if os.path.exists(p):
                font_path = p.replace('\\', '/').replace(':', '\\:')
                break
        if not font_path:
            font_path = "sans"
    
    safe_text = watermark_text or 'Watermark'
    for ch in ["'", ":", "\\", "%"]:
        safe_text = safe_text.replace(ch, f"\\{ch}")
        
    has_backsound = backsound and Path(backsound).exists() and Path(backsound).stat().st_size > 0
    
    position_map = {
        'top-right':     ('x=W-tw-30', 'y=30'),
        'top-left':      ('x=30', 'y=30'),
        'top-center':    ('x=(W-tw)/2', 'y=30'),
        'center':        ('x=(W-tw)/2', 'y=(H-th)/2'),
        'bottom-right':  ('x=W-tw-30', 'y=H-th-30'),
        'bottom-left':   ('x=30', 'y=H-th-30'),
        'bottom-center': ('x=(W-tw)/2', 'y=H-th-30'),
    }

    if watermark_position == 'random':
        # Cycle through 6 center-friendly positions every 5 seconds (30s cycle)
        # Starts exactly at the center, then shifts subtly (120px - 150px) around the center, staying relatively close to it
        # 1. Center, 2. Center-Right-Up, 3. Center-Left-Down, 4. Center-Right-Down, 5. Center-Left-Up, 6. Center
        wm_x = "x='if(lt(mod(t\\,30)\\,5)\\,(W-tw)/2\\,if(lt(mod(t\\,30)\\,10)\\,(W-tw)/2+150\\,if(lt(mod(t\\,30)\\,15)\\,(W-tw)/2-120\\,if(lt(mod(t\\,30)\\,20)\\,(W-tw)/2+120\\,if(lt(mod(t\\,30)\\,25)\\,(W-tw)/2-150\\,(W-tw)/2)))))'"
        wm_y = "y='if(lt(mod(t\\,30)\\,5)\\,(H-th)/2\\,if(lt(mod(t\\,30)\\,10)\\,(H-th)/2-120\\,if(lt(mod(t\\,30)\\,15)\\,(H-th)/2+150\\,if(lt(mod(t\\,30)\\,20)\\,(H-th)/2+120\\,if(lt(mod(t\\,30)\\,25)\\,(H-th)/2-150\\,(H-th)/2)))))'"
        logo_x = "x='if(lt(mod(t\\,30)\\,5)\\,(W-w)/2\\,if(lt(mod(t\\,30)\\,10)\\,(W-w)/2+150\\,if(lt(mod(t\\,30)\\,15)\\,(W-w)/2-120\\,if(lt(mod(t\\,30)\\,20)\\,(W-w)/2+120\\,if(lt(mod(t\\,30)\\,25)\\,(W-w)/2-150\\,(W-w)/2)))))'"
        logo_y = "y='if(lt(mod(t\\,30)\\,5)\\,(H-h)/2\\,if(lt(mod(t\\,30)\\,10)\\,(H-h)/2-120\\,if(lt(mod(t\\,30)\\,15)\\,(H-h)/2+150\\,if(lt(mod(t\\,30)\\,20)\\,(H-h)/2+120\\,if(lt(mod(t\\,30)\\,25)\\,(H-h)/2-150\\,(H-h)/2)))))'"
    else:
        pos = position_map.get(watermark_position, position_map['top-right'])
        wm_x, wm_y = pos[0], pos[1]
        logo_x, logo_y = "x=W-w-30", "y=30"

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

    thumbnail_index = -1
    has_thumbnail = thumbnail_path and Path(thumbnail_path).exists()
    if has_thumbnail:
        input_args += ['-loop', '1', '-i', thumbnail_path]
        thumbnail_index = next_index
        next_index += 1

    # Base background and video blending filter (Anti-Copyright & Speed Ramping Engine)
    import random
    import math
    
    pre_filter = ""
    last_v = "[0:v]"
    
    # Check speed ramping setting
    if str(use_speed_ramping).lower() == 'true':
        seg_dur = 3.0
        num_segments = int(video_dur // seg_dur)
        if num_segments >= 2:
            segment_tags = []
            for i in range(num_segments):
                start = i * seg_dur
                end = (i + 1) * seg_dur if i < num_segments - 1 else video_dur
                speed = random.uniform(0.85, 1.15)
                tag = f"[v_seg{i}]"
                pre_filter += f"[0:v]trim=start={start:.2f}:end={end:.2f},setpts={speed:.3f}*(PTS-STARTPTS){tag};"
                segment_tags.append(tag)
            
            concat_inputs = "".join(segment_tags)
            pre_filter += f"{concat_inputs}concat=n={num_segments}:v=1:a=0[v_ramped];"
            last_v = "[v_ramped]"
        else:
            speed = random.uniform(0.85, 1.15)
            pre_filter += f"[0:v]setpts={speed:.3f}*PTS[v_ramped];"
            last_v = "[v_ramped]"
            
    # Check camera shake setting
    shake_val = str(use_camera_shake).lower()
    if shake_val in ('true', 'normal', 'slow', 'fast'):
        # determine amplitude and frequency
        if shake_val == 'slow':
            amp, freq = 4, 1.5
        elif shake_val == 'fast':
            amp, freq = 12, 5
        else: # true or normal
            amp, freq = 8, 3
        
        crop_w = f"iw-{2*amp}"
        crop_h = f"ih-{2*amp}"
        crop_x = f"'{amp}+{amp}*sin(2*PI*t*{freq})'"
        crop_y = f"'{amp}+{amp}*cos(2*PI*t*{freq})'"
        
        pre_filter += f"{last_v}crop=w={crop_w}:h={crop_h}:x={crop_x}:y={crop_y}[v_processed]"
        last_v_processed = "[v_processed]"
    else:
        if pre_filter:
            pre_filter += f"{last_v}null[v_processed]"
            last_v_processed = "[v_processed]"
        else:
            last_v_processed = "[0:v]"

    # Base background and video blending filter using last_v_processed (with split filter to support multiple outputs from internal pads)
    if pre_filter:
        filter_complex = (
            pre_filter + ";"
            f"{last_v_processed}split=2[v_split_bg][v_split_main];"
            f"[v_split_bg]scale=720:1280,boxblur=20:5[bg];"
            f"[v_split_main]scale=640:1136[main];"
            f"[bg][main]overlay=(W-w)/2:(H-h)/2[vid_with_bg]"
        )
    else:
        filter_complex = (
            f"[0:v]scale=720:1280,boxblur=20:5[bg];"
            f"[0:v]scale=640:1136[main];"
            f"[bg][main]overlay=(W-w)/2:(H-h)/2[vid_with_bg]"
        )
    last_vid = "[vid_with_bg]"
    
    # 1. Overlay logo watermark if present
    if logo_index != -1:
        logo_out = "[vid_logo]"
        filter_complex += (
            f";[{logo_index}:v]scale=100:-1,format=rgba,colorchannelmixer=aa={wm_opacity}[logo];"
            f"{last_vid}[logo]overlay={logo_x}:{logo_y}{logo_out}"
        )
        last_vid = logo_out
        
    # 2. Draw text watermark if present
    if watermark_text:
        text_out = "[vid_text]"
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
                f";{last_vid}subtitles='{srt_escaped}'[vid_subs]"
            )
        else:
            ass_primary = convert_to_ass_color(sub_color, sub_opacity)
            ass_secondary = convert_to_ass_color(sub_sec_color, sub_opacity)
            filter_complex += (
                f";{last_vid}subtitles='{srt_escaped}':"
                f"force_style='FontName={sub_font},FontSize={sub_size},"
                f"PrimaryColour={ass_primary},SecondaryColour={ass_secondary},"
                f"OutlineColour=&H00000000,Outline=2,Shadow=0,Alignment=2,"
                f"MarginV=280'[vid_subs]"
            )
        last_vid = "[vid_subs]"

    # 4. Overlay thumbnail cover if present
    if thumbnail_index != -1:
        thumb_out = "[vid_thumb]"
        filter_complex += (
            f";[{thumbnail_index}:v]scale=720:1280:force_original_aspect_ratio=decrease,"
            f"pad=720:1280:(720-iw)/2:(1280-ih)/2:color=black,format=rgba,"
            f"fade=out:st=0.8:d=0.2:alpha=1[thumb];"
            f"{last_vid}[thumb]overlay=0:0:enable='lt(t,1.0)'{thumb_out}"
        )
        last_vid = thumb_out
        
    # Final routing to [vid_final]
    if last_vid != "[vid_final]":
        filter_complex += f";{last_vid}null[vid_final]"

    # Audio mixing mapping using the tracked backsound index
    if backsound_index != -1:
        filter_complex += (
            f";[{backsound_index}:0]volume={backsound_volume:.2f}[bg_audio];"
            f"[1:0][bg_audio]amix=inputs=2:duration=first[aud_final]"
        )
        audio_map = '[aud_final]'
    else:
        audio_map = '1:0'

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
        '-t', t_arg,
        '-movflags', '+faststart',
        output_path
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if job_id:
        register_task_pid(job_id, process.pid)
        
    try:
        stdout, stderr = process.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise RuntimeError("FFmpeg process timed out (300 seconds limit exceeded).")
    finally:
        if job_id:
            unregister_task_pid(job_id)
            
    if process.returncode != 0:
        stderr_last = stderr if stderr else 'No stderr'
        cmd_str = " ".join(cmd)
        raise RuntimeError(f"FFmpeg render failed. Command run: {cmd_str}. Error: {stderr_last}")
        
    if not Path(output_path).exists():
        raise RuntimeError("FFmpeg completed but output file was not found.")


def step_ffmpeg_compress(
    input_video: str,
    output_path: str,
    crf: int = 26,
    job_id: Optional[str] = None
):
    """Compress video using FFmpeg: converts any video format to MP4 with optimal CRF size reduction."""
    cmd = [
        FFMPEG_PATH, '-y',
        '-i', input_video,
        '-c:v', 'libx264',
        '-crf', str(crf),
        '-preset', 'fast',
        '-vf', "scale='min(1920,iw)':-2",  # Scale down width to max 1920, maintain aspect ratio
        '-c:a', 'aac',
        '-b:a', '128k',
        '-movflags', '+faststart',
        output_path
    ]
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if job_id:
        register_task_pid(job_id, process.pid)
        
    try:
        stdout, stderr = process.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise RuntimeError("FFmpeg compression process timed out (300 seconds limit exceeded).")
    finally:
        if job_id:
            unregister_task_pid(job_id)
            
    if process.returncode != 0:
        stderr_last = stderr[-1500:] if stderr else 'No stderr'
        raise RuntimeError(f"FFmpeg compression failed. Error: {stderr_last[-300:]}")
        
    if not Path(output_path).exists():
        raise RuntimeError("FFmpeg completed but compressed file was not found.")

