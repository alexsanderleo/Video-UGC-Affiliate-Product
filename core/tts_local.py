import os
import sys
import asyncio
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent.parent.resolve()
MODELS_DIR = BASE_DIR / "models"
PIPER_DIR = MODELS_DIR / "piper"

# Ensure models directory exists
PIPER_DIR.mkdir(parents=True, exist_ok=True)

# Piper Indonesian model URLs
PIPER_MODEL_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/id/id_ID/news_tts/medium/id_ID-news_tts-medium.onnx"
PIPER_CONFIG_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/id/id_ID/news_tts/medium/id_ID-news_tts-medium.onnx.json"

PIPER_MODEL_PATH = PIPER_DIR / "id_ID-news_tts-medium.onnx"
PIPER_CONFIG_PATH = PIPER_DIR / "id_ID-news_tts-medium.onnx.json"

def download_file(url: str, dest_path: Path):
    """Download a file with progress print."""
    print(f"[TTS Local] Downloading {url} -> {dest_path}...")
    try:
        urllib.request.urlretrieve(url, str(dest_path))
        print(f"[TTS Local] Download completed successfully.")
    except Exception as e:
        print(f"[TTS Local] Download failed: {e}")
        raise RuntimeError(f"Gagal mengunduh model local TTS: {e}")

def ensure_piper_model():
    """Ensure Piper Indonesian voice model files are downloaded."""
    if not PIPER_MODEL_PATH.exists():
        download_file(PIPER_MODEL_URL, PIPER_MODEL_PATH)
    if not PIPER_CONFIG_PATH.exists():
        download_file(PIPER_CONFIG_URL, PIPER_CONFIG_PATH)



def convert_wav_to_mp3(ffmpeg_path: str, wav_path: str, mp3_path: str):
    """Convert raw WAV audio to MP3 using local FFmpeg."""
    cmd = [
        ffmpeg_path,
        "-y",
        "-i", wav_path,
        "-codec:a", "libmp3lame",
        "-qscale:a", "2",
        mp3_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg conversion failed: {result.stderr}")

async def generate_gtts(text: str, output_path: str):
    """Generate TTS using gTTS (Google Translate) Indonesian."""
    import asyncio
    from gtts import gTTS
    
    def run():
        tts = gTTS(text=text, lang='id')
        tts.save(output_path)
        
    await asyncio.to_thread(run)

async def generate_piper(text: str, output_path: str, ffmpeg_path: str):
    """Generate TTS using Piper (Indonesian news_tts-medium)."""
    import asyncio
    
    # Ensure model is available
    ensure_piper_model()
    
    # Piper outputs WAV, so generate WAV first in temp
    temp_wav_path = Path(output_path).with_suffix(".wav")
    
    def run_piper_cli():
        cmd = [
            sys.executable, "-m", "piper",
            "-m", str(PIPER_MODEL_PATH),
            "-c", str(PIPER_CONFIG_PATH),
            "-f", str(temp_wav_path)
        ]
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8"
        )
        stdout, stderr = process.communicate(input=text)
        if process.returncode != 0:
            raise RuntimeError(f"Piper execution failed: {stderr}")
            
    # Run synthesis in background thread
    await asyncio.to_thread(run_piper_cli)
    
    # Convert WAV to MP3
    def run_conversion():
        convert_wav_to_mp3(ffmpeg_path, str(temp_wav_path), output_path)
        # Clean up temporary WAV file
        if temp_wav_path.exists():
            temp_wav_path.unlink()
            
    await asyncio.to_thread(run_conversion)


_tts_instance = None
_tts_lock = asyncio.Lock()

async def get_supertonic_tts_instance():
    global _tts_instance
    if _tts_instance is None:
        async with _tts_lock:
            if _tts_instance is None:
                from supertonic import TTS
                print("[TTS Local] Initializing Supertonic 3 TTS engine globally...")
                _tts_instance = TTS(auto_download=True)
    return _tts_instance

async def generate_supertonic(text: str, output_path: str, voice: str):
    """Generate TTS using Supertonic 3 local ONNX engine in natural Indonesian."""
    import asyncio
    
    # Preset voice styles: F1-F5 (female), M1-M5 (male)
    voice_name = "F1"
    for preset in ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"]:
        if preset.lower() in voice.lower():
            voice_name = preset
            break
            
    is_male = voice_name.startswith("M")
    fallback_voice = "id-ID-ArdiNeural" if is_male else "id-ID-GadisNeural"
    
    try:
        print(f"[TTS Local] Attempting Supertonic 3 synthesis (Voice: {voice_name}) for text: {text[:50]}...")
        
        # Get cached global TTS instance
        tts = await get_supertonic_tts_instance()
        
        def run_synthesis():
            style = tts.get_voice_style(voice_name=voice_name)
            wav, duration = tts.synthesize(text, voice_style=style, lang="id")
            temp_wav_path = Path(output_path).with_suffix(".wav")
            tts.save_audio(wav, str(temp_wav_path))
            return temp_wav_path
            
        temp_wav_path = await asyncio.to_thread(run_synthesis)
        
        # Determine FFmpeg path safely
        ffmpeg_exe = "ffmpeg"
        try:
            from core.pipeline import FFMPEG_PATH
            ffmpeg_exe = FFMPEG_PATH
        except ImportError:
            try:
                from app import FFMPEG_PATH
                ffmpeg_exe = FFMPEG_PATH
            except ImportError:
                import shutil
                found = shutil.which("ffmpeg")
                if found:
                    ffmpeg_exe = found
                    
        # Convert WAV to MP3 using local FFmpeg
        def run_conversion():
            convert_wav_to_mp3(ffmpeg_exe, str(temp_wav_path), output_path)
            if temp_wav_path.exists():
                temp_wav_path.unlink()
                
        await asyncio.to_thread(run_conversion)
        print(f"[TTS Local] Supertonic 3 synthesis completed successfully.")
        
    except Exception as e:
        print(f"[TTS Local ERROR] Supertonic 3 failed: {e}")
        print(f"[TTS Local WARNING] Falling back with timeout protection...")
        
        # Fallback 1: Try Edge-TTS with strict 30s timeout (prevents infinite hang on blocked VPS IPs)
        try:
            import edge_tts
            print(f"[TTS Local FALLBACK 1] Trying Edge-TTS ({fallback_voice}) with 30s timeout...")
            communicate = edge_tts.Communicate(text, fallback_voice)
            await asyncio.wait_for(communicate.save(output_path), timeout=30)
            print(f"[TTS Local FALLBACK 1] Edge-TTS synthesis completed successfully.")
            return
        except (asyncio.TimeoutError, Exception) as e2:
            print(f"[TTS Local FALLBACK 1 FAILED] Edge-TTS failed/timed out: {e2}")
            # Clean up partial file
            if Path(output_path).exists():
                Path(output_path).unlink()
        
        # Fallback 2: gTTS (Google Translate — very reliable, never blocks VPS IPs)
        try:
            print(f"[TTS Local FALLBACK 2] Using gTTS (Google Translate)...")
            await generate_gtts(text, output_path)
            print(f"[TTS Local FALLBACK 2] gTTS synthesis completed successfully.")
            return
        except Exception as e3:
            print(f"[TTS Local FALLBACK 2 FAILED] gTTS failed: {e3}")
        
        raise RuntimeError(f"Semua mesin TTS gagal. Supertonic: {e}")


