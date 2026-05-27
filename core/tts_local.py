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

async def generate_xtts_v2(text: str, output_path: str, voice: str = "xtts-clone-agomart"):
    """Generate TTS using local XTTS v2 voice cloning."""
    import asyncio
    
    # Auto-agree to Coqui TTS Terms of Service to prevent interactive prompt crash in Celery worker
    os.environ["COQUI_TOS_AGREED"] = "1"
    
    # Register safe globals and monkeypatch torch.load for PyTorch 2.6+ to prevent "Weights only load failed" serialization crash
    import torch
    try:
        # 1. Monkeypatch torch.load and torch.serialization.load to force weights_only=False
        original_load = torch.load
        def patched_load(f, *args, **kwargs):
            kwargs["weights_only"] = False
            return original_load(f, *args, **kwargs)
        torch.load = patched_load
        if hasattr(torch, "serialization") and hasattr(torch.serialization, "load"):
            torch.serialization.load = patched_load
        print("[XTTS Patched Load] Successfully monkeypatched torch.load to weights_only=False")
    except Exception as e:
        print(f"[XTTS Patched Load] Warning: {e}")

    try:
        from TTS.tts.configs.xtts_config import XttsConfig
        if hasattr(torch, "serialization") and hasattr(torch.serialization, "add_safe_globals"):
            torch.serialization.add_safe_globals([XttsConfig])
    except Exception as e:
        print(f"[XTTS Safe Globals] Warning: {e}")
    # Force torchaudio to use the 'soundfile' backend to prevent PyTorch 2.9+ / torchaudio torchcodec errors
    import torchaudio
    try:
        torchaudio.set_audio_backend("soundfile")
        print("[XTTS Torchaudio Backend] Successfully set backend to soundfile")
    except Exception as e:
        print(f"[XTTS Torchaudio Backend] Warning setting backend: {e}")

    try:
        from TTS.api import TTS
    except ImportError:
        raise RuntimeError(
            "Library 'TTS' (Coqui TTS) tidak terinstal di server.\n"
            "Silakan instal Coqui TTS dengan perintah: 'pip install TTS'\n"
            "dan pastikan server memiliki memori/CPU yang cukup."
        )
        
    # Reference voice sample path for cloning
    ref_dir = BASE_DIR / "static" / "voices"
    ref_dir.mkdir(parents=True, exist_ok=True)
    
    if "male" in voice:
        ref_wav_path = ref_dir / "reference_male.wav"
        default_ref_url = "https://huggingface.co/coqui/XTTS-v2/resolve/main/samples/en_sample.wav"
    else:
        ref_wav_path = ref_dir / "reference.wav"
        default_ref_url = "https://github.com/coqui-ai/TTS/raw/main/tests/data/ljspeech/wavs/LJ001-0001.wav"
    
    if not ref_wav_path.exists():
        # Automatically download high-quality reference voice from GitHub!
        print(f"[TTS Local] Reference voice not found. Downloading default: {default_ref_url} -> {ref_wav_path}...")
        try:
            download_file(default_ref_url, ref_wav_path)
            print(f"[TTS Local] Default reference voice downloaded successfully.")
        except Exception as e:
            if "male" in voice and (ref_dir / "reference.wav").exists():
                ref_wav_path = ref_dir / "reference.wav"
            else:
                raise RuntimeError(
                    f"File contoh kloning suara tidak ditemukan di: {ref_wav_path} dan gagal diunduh otomatis.\n"
                    f"Detail: {e}\n"
                    "Silakan taruh file suara contoh format WAV (durasi 5-10 detik) "
                    "dengan nama 'reference.wav' atau 'reference_male.wav' di dalam folder 'static/voices/' untuk mulai kloning."
                )
        
    temp_wav_path = Path(output_path).with_suffix(".wav")
    
    def run_xtts():
        # Load XTTS v2 model (will download automatically on first run)
        tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
        # XTTS v2 does not support Indonesian ('id'). We use 'en' (English) as the language code fallback.
        # This synthesizes the Indonesian text using the English phonetic model, producing a high-quality cloned voice.
        tts.tts_to_file(
            text=text,
            speaker_wav=str(ref_wav_path),
            language="en",
            file_path=str(temp_wav_path)
        )
        
    # Run in background thread because XTTS is extremely CPU intensive
    await asyncio.to_thread(run_xtts)
    
    # Convert WAV to MP3 using ffmpeg
    from core.pipeline import FFMPEG_PATH
    def run_conversion():
        convert_wav_to_mp3(FFMPEG_PATH, str(temp_wav_path), output_path)
        if temp_wav_path.exists():
            temp_wav_path.unlink()
            
    await asyncio.to_thread(run_conversion)
