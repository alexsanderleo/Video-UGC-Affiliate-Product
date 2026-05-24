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

# ============================================================
# Directory Configuration
# ============================================================
BASE_DIR = Path(__file__).parent.parent.resolve()
UPLOAD_DIR = BASE_DIR / 'uploads'
OUTPUT_DIR = BASE_DIR / 'outputs'
TEMP_DIR = BASE_DIR / 'temp'
BACKSOUND_PATH = BASE_DIR / 'musik_backsound.mp3'

# Ensure directories exist
for d in [UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR]:
    d.mkdir(exist_ok=True)

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

# ============================================================
# Pipeline Core Logic Functions
# ============================================================

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

def ensure_backsound() -> str:
    """Create a silent MP3 backsound placeholder if no file exists."""
    if BACKSOUND_PATH.exists():
        return str(BACKSOUND_PATH)
    
    silent_path = TEMP_DIR / 'silent_backsound.mp3'
    if silent_path.exists():
        return str(silent_path)
    
    try:
        cmd = [
            FFMPEG_PATH, '-y',
            '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
            '-t', '60',
            '-c:a', 'libmp3lame', '-b:a', '32k',
            str(silent_path)
        ]
        subprocess.run(cmd, capture_output=True, timeout=15)
        if silent_path.exists():
            return str(silent_path)
    except Exception as e:
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
        max_tokens=1024
    )

    return response.choices[0].message.content

async def step_b_tts(text: str, voice: str, output_path: str):
    """Convert text to speech using Edge-TTS asynchronously."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)
    
    if not Path(output_path).exists():
        raise RuntimeError("Edge-TTS failed to produce the audio file.")

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

def generate_srt(narration_text: str, audio_duration: float, output_srt_path: str) -> str:
    """Generate SRT subtitle file synchronized to the audio duration."""
    segments = split_text_to_sentences(narration_text)
    total_chars = sum(len(s) for s in segments)
    if total_chars == 0:
        total_chars = 1
        
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

def step_c_ffmpeg(
    input_video: str,
    tts_audio: str,
    backsound: str,
    watermark_text: str,
    watermark_mode: str,
    watermark_logo: str,
    output_path: str,
    watermark_position: str = 'top-right',
    subtitle_path: str = None
):
    """Process video using FFmpeg with anti-copyright and watermark overlay."""
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
        
    has_backsound = backsound and Path(backsound).exists()
    
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
        wm_x = "x='if(lt(mod(t\\,20)\\,5)\\,W-tw-30\\,if(lt(mod(t\\,20)\\,10)\\,30\\,if(lt(mod(t\\,20)\\,15)\\,W-tw-30\\,30)))'"
        wm_y = "y='if(lt(mod(t\\,20)\\,5)\\,30\\,if(lt(mod(t\\,20)\\,10)\\,30\\,if(lt(mod(t\\,20)\\,15)\\,H-th-30\\,H-th-30)))'"
    else:
        pos = position_map.get(watermark_position, position_map['top-right'])
        wm_x, wm_y = pos[0], pos[1]

    # Build filters
    if watermark_mode == 'logo' and watermark_logo and Path(watermark_logo).exists():
        filter_complex = (
            f"[0:v]scale=720:1280,boxblur=20:5[bg];"
            f"[0:v]scale=640:1136[main];"
            f"[bg][main]overlay=(W-w)/2:(H-h)/2[vid_with_bg];"
        )
        if has_backsound:
            filter_complex += (
                f"[3:v]scale=100:-1[logo];"
                f"[vid_with_bg][logo]overlay=W-w-30:30[vid_final]"
            )
            input_args = ['-i', input_video, '-i', tts_audio, '-i', backsound, '-i', watermark_logo]
        else:
            filter_complex += (
                f"[2:v]scale=100:-1[logo];"
                f"[vid_with_bg][logo]overlay=W-w-30:30[vid_final]"
            )
            input_args = ['-i', input_video, '-i', tts_audio, '-i', watermark_logo]
    else:
        filter_complex = (
            f"[0:v]scale=720:1280,boxblur=20:5[bg];"
            f"[0:v]scale=640:1136[main];"
            f"[bg][main]overlay=(W-w)/2:(H-h)/2[vid_with_bg];"
            f"[vid_with_bg]drawtext=text='{safe_text}':"
            f"fontfile={font_path}:"
            f"fontsize=24:fontcolor=white@0.65:"
            f"{wm_x}:{wm_y}[vid_wm]"
        )
        if subtitle_path and Path(subtitle_path).exists():
            srt_escaped = subtitle_path.replace('\\', '/').replace(':', '\\:')
            filter_complex += (
                f";[vid_wm]subtitles='{srt_escaped}':"
                f"force_style='FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,"
                f"OutlineColour=&H00000000,Outline=2,Shadow=1,Alignment=2,"
                f"MarginV=60'[vid_final]"
            )
        else:
            filter_complex = filter_complex.replace('[vid_wm]', '[vid_final]')
            
        input_args = ['-i', input_video, '-i', tts_audio]
        if has_backsound:
            input_args += ['-i', backsound]

    if has_backsound:
        filter_complex += (
            f";[2:a]volume=0.12[bg_audio];"
            f"[1:a][bg_audio]amix=inputs=2:duration=first[aud_final]"
        )
        audio_map = '[aud_final]'
    else:
        audio_map = '1:a'

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

    process = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if process.returncode != 0:
        stderr_last = process.stderr[-1500:] if process.stderr else 'No stderr'
        raise RuntimeError(f"FFmpeg render failed. Error: {stderr_last[-300:]}")
        
    if not Path(output_path).exists():
        raise RuntimeError("FFmpeg completed but output file was not found.")
