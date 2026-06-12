"""
Tahap D — Auto Reframe 9:16 (speaker tracking).

- Sampling 2 frame/detik, deteksi wajah YuNet (OpenCV — model ONNX di-download otomatis).
- Smoothing posisi moving-average supaya crop tidak goyang.
- Output crop keyframes [{t, cx, cy}] per klip (t relatif ke video SUMBER, cx/cy = pusat
  crop dalam piksel sumber). Tanpa wajah -> center crop.
Catatan: MediaPipe tidak mendukung Python 3.13, jadi dipakai YuNet (setara, ringan).
"""

import logging
import urllib.request
from pathlib import Path

from core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
YUNET_PATH = settings.BASE_DIR / "models_cache" / "face_detection_yunet_2023mar.onnx"

SAMPLE_FPS = 2.0          # 2 frame per detik sesuai spec
SMOOTH_WINDOW = 5         # moving average 5 sampel (~2.5 detik)


def _ensure_model() -> Path:
    if not YUNET_PATH.exists():
        YUNET_PATH.parent.mkdir(parents=True, exist_ok=True)
        logger.info("[ClipStudio] Download model YuNet ...")
        urllib.request.urlretrieve(YUNET_URL, str(YUNET_PATH))
    return YUNET_PATH


def _moving_average(points: list, window: int) -> list:
    """Smoothing (cx, cy) dengan moving average."""
    if len(points) <= 2:
        return points
    sm = []
    half = window // 2
    for i in range(len(points)):
        s = max(0, i - half)
        e = min(len(points), i + half + 1)
        xs = [p[0] for p in points[s:e]]
        ys = [p[1] for p in points[s:e]]
        sm.append((sum(xs) / len(xs), sum(ys) / len(ys)))
    return sm


def compute_crop_keyframes(source_path: str, start: float, end: float,
                           width: int, height: int) -> list:
    """
    Deteksi wajah pada rentang [start, end] -> [{t, cx, cy}] (sudah di-smooth).
    Frame diambil lewat PIPE ffmpeg (fps=2 + scale 320) sekali jalan — jauh lebih
    cepat daripada seek acak OpenCV per sampel (decode dari keyframe tiap seek).
    """
    import subprocess
    import numpy as np
    import cv2

    cx_default, cy_default = width / 2, height / 2
    try:
        model_path = _ensure_model()
        detector = cv2.FaceDetectorYN.create(str(model_path), "", (320, 320), 0.6)
    except Exception as e:
        logger.warning("[ClipStudio] YuNet tidak tersedia (%s) — center crop.", e)
        return [{"t": round(start, 2), "cx": cx_default, "cy": cy_default}]

    det_w = 320
    scale = det_w / max(1, width)
    det_h = max(2, int(height * scale) // 2 * 2)
    detector.setInputSize((det_w, det_h))

    dur = max(0.1, end - start)
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
         "-i", str(source_path),
         "-vf", f"fps={SAMPLE_FPS},scale={det_w}:{det_h}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    frame_bytes = det_w * det_h * 3
    times, centers = [], []
    t = start
    step = 1.0 / SAMPLE_FPS
    last_center = (cx_default, cy_default)
    while t < end:
        buf = proc.stdout.read(frame_bytes)
        if not buf or len(buf) < frame_bytes:
            break
        small = np.frombuffer(buf, dtype=np.uint8).reshape((det_h, det_w, 3))
        try:
            _, faces = detector.detect(small)
        except Exception:
            faces = None
        if faces is not None and len(faces) > 0:
            # Pilih wajah dengan skor*luas terbesar (pembicara utama)
            best = max(faces, key=lambda f: float(f[14]) * float(f[2]) * float(f[3]))
            fx, fy, fw, fh = best[0] / scale, best[1] / scale, best[2] / scale, best[3] / scale
            last_center = (fx + fw / 2, fy + fh / 2)
        times.append(round(t, 2))
        centers.append(last_center)
        t += step
    proc.stdout.close()
    proc.wait()

    if not centers:
        return [{"t": round(start, 2), "cx": cx_default, "cy": cy_default}]

    centers = _moving_average(centers, SMOOTH_WINDOW)
    # float() wajib: hasil deteksi YuNet bertipe numpy.float32 yang tidak bisa di-serialize JSON
    keyframes = [
        {"t": float(times[i]), "cx": round(float(centers[i][0]), 1), "cy": round(float(centers[i][1]), 1)}
        for i in range(len(times))
    ]

    # Rapikan: buang keyframe yang nyaris tidak bergerak (< 2% lebar) agar data ringkas
    slim = [keyframes[0]]
    thresh = width * 0.02
    for kf in keyframes[1:]:
        if abs(kf["cx"] - slim[-1]["cx"]) > thresh or abs(kf["cy"] - slim[-1]["cy"]) > thresh:
            slim.append(kf)
    if slim[-1]["t"] != keyframes[-1]["t"]:
        slim.append(keyframes[-1])
    return slim


def crop_window(cx: float, cy: float, src_w: int, src_h: int, aspect: str = "9:16"):
    """Hitung window crop (x, y, w, h) berpusat (cx, cy) untuk aspect ratio target."""
    ratios = {"9:16": 9 / 16, "1:1": 1.0, "16:9": 16 / 9}
    r = ratios.get(aspect, 9 / 16)
    # Window terbesar dengan rasio r yang muat di sumber
    if src_w / src_h > r:
        h = src_h
        w = h * r
    else:
        w = src_w
        h = w / r
    x = min(max(0, cx - w / 2), src_w - w)
    y = min(max(0, cy - h / 2), src_h - h)
    return int(round(x)), int(round(y)), int(round(w)), int(round(h))
