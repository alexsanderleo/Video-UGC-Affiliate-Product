import os
import subprocess
from pathlib import Path

BASE_DIR = Path("/www/wwwroot/video.agomart.com")
UPLOADS_DIR = BASE_DIR / "uploads"
TEMP_DIR = BASE_DIR / "temp"
BACKSOUNDS_DIR = BASE_DIR / "backsounds"

def get_latest_file(directory, pattern):
    files = list(Path(directory).glob(pattern))
    if not files:
        return None
    return max(files, key=os.path.getmtime)

print("=" * 60)
print(" DIAGNOSTIC FFmpeg ENGINE FOR AULEX VPS")
print("=" * 60)

# Find latest files to diagnose
input_video = get_latest_file(UPLOADS_DIR, "*.mp4")
tts_audio = get_latest_file(TEMP_DIR, "*.mp3")
backsound = BACKSOUNDS_DIR / "backsound1.mp3"

print(f"Latest Input Video: {input_video} (Size: {input_video.stat().st_size if input_video else 'N/A'} bytes)")
print(f"Latest Narration:    {tts_audio} (Size: {tts_audio.stat().st_size if tts_audio else 'N/A'} bytes)")
print(f"Backsound 1:        {backsound} (Size: {backsound.stat().st_size if backsound.exists() else 'N/A'} bytes)")

def run_ffprobe(filepath):
    if not filepath or not Path(filepath).exists():
        print(f"\n[FFPROBE] File does not exist: {filepath}")
        return
    print(f"\n[FFPROBE] Analyzing {Path(filepath).name}:")
    cmd = [
        "ffprobe", "-v", "error", 
        "-show_entries", "stream=index,codec_type,codec_name,channels,sample_rate", 
        "-of", "json", str(filepath)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(res.stdout)
    else:
        print(f"ERROR: {res.stderr}")

run_ffprobe(input_video)
run_ffprobe(tts_audio)
run_ffprobe(backsound)

if input_video and tts_audio and backsound.exists():
    print("\n" + "=" * 60)
    print(" RUNNING FFmpeg TEST WITH FULL ERROR CAPTURE")
    print("=" * 60)
    
    # We will construct a minimal filter graph that matches the user's error trace
    # and run it to see the exact error.
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_video),
        "-i", str(tts_audio),
        "-i", str(backsound),
        "-filter_complex", 
        "[0:v]scale=720:1280[bg];[0:v]scale=640:1136[main];[bg][main]overlay=(W-w)/2:(H-h)/2[vid_final];"
        "[2:0]volume=0.12[bg_audio];[1:0][bg_audio]amix=inputs=2:duration=first[aud_final]",
        "-map", "[vid_final]",
        "-map", "[aud_final]",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac",
        "-t", "5",
        str(BASE_DIR / "outputs" / "test_diag_output.mp4")
    ]
    
    print(f"Running command: {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    print("\n--- FFmpeg stdout ---")
    print(res.stdout)
    print("\n--- FFmpeg stderr (FULL) ---")
    print(res.stderr)
else:
    print("\n[ERROR] Missing files to run FFmpeg test.")
