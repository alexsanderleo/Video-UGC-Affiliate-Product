import os
import sys
import asyncio
from pathlib import Path

BASE_DIR = Path("/www/wwwroot/video.agomart.com")
sys.path.insert(0, str(BASE_DIR))

from core.pipeline import step_b_tts, get_audio_duration
import subprocess

async def test():
    print("=" * 60)
    print(" TESTING TTS GENERATION & STREAM INTEGRITY")
    print("=" * 60)
    
    text = "Hai semuanya! Produk ini sangat luar biasa dan sangat viral, silakan diorder sekarang juga!"
    voice = "id-ID-GadisNeural"
    output_path = str(BASE_DIR / "temp" / "test_debug_narasi.mp3")
    srt_path = str(BASE_DIR / "temp" / "test_debug_subtitles.ass")
    
    # Clean up previous test files if any
    for p in [output_path, srt_path]:
        if Path(p).exists():
            Path(p).unlink()
            
    print(f"Target Output Path: {output_path}")
    print(f"Target Subtitle Path: {srt_path}")
    
    try:
        print("\n[Step B] Calling step_b_tts...")
        await step_b_tts(
            text=text,
            voice=voice,
            output_path=output_path,
            srt_path=srt_path
        )
        print("step_b_tts completed successfully.")
        
        # Check files exist
        out_file = Path(output_path)
        sub_file = Path(srt_path)
        print(f"\n[FILE STATUS]")
        print(f"TTS Output Exists: {out_file.exists()} (Size: {out_file.stat().st_size if out_file.exists() else 0} bytes)")
        print(f"Subtitles Exists: {sub_file.exists()} (Size: {sub_file.stat().st_size if sub_file.exists() else 0} bytes)")
        
        # Probe using ffprobe
        if out_file.exists():
            print(f"\n[FFPROBE] Probing generated TTS MP3:")
            cmd = [
                "ffprobe", "-v", "error", 
                "-show_entries", "stream=index,codec_type,codec_name,channels,sample_rate", 
                "-of", "json", str(output_path)
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            print(res.stdout)
            
            # Check duration
            dur = get_audio_duration(str(output_path))
            print(f"Detected Duration: {dur} seconds")
            
    except Exception as e:
        import traceback
        print(f"\n[ERROR OCCURRED] during TTS:")
        traceback.print_exc()

asyncio.run(test())
