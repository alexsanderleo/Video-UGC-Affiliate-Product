import os
import sys
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


