# -*- coding: utf-8 -*-
"""Composite only moving character pixels from H3 back onto the clean page."""
import argparse
import json
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
PLAN = SCRIPT_DIR / "character-beats.json"
H3_DIR = ROOT / "03-镜头分段" / "character-h3"
OUT_DIR = ROOT / "03-镜头分段" / "character-motion-beats"
WIDTH = 1344
HEIGHT = 768
FPS = 24


def original_mask(image, threshold=12):
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    border = max(4, min(array.shape[0], array.shape[1]) // 30)
    border_pixels = np.concatenate(
        [
            array[:border].reshape(-1, 3),
            array[-border:].reshape(-1, 3),
            array[:, :border].reshape(-1, 3),
            array[:, -border:].reshape(-1, 3),
        ]
    )
    background = np.median(border_pixels, axis=0)
    content = np.max(np.abs(array - background), axis=2) > threshold
    mask = Image.fromarray((content * 255).astype(np.uint8), "L")
    mask = mask.filter(ImageFilter.MaxFilter(21))
    return np.asarray(mask, dtype=np.float32) / 255.0


def motion_mask(frames, threshold=2.5):
    mean = np.zeros(frames[0].shape[:2], dtype=np.float32)
    m2 = np.zeros_like(mean)
    for index, frame in enumerate(frames, 1):
        gray = frame.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        delta = gray - mean
        mean += delta / index
        m2 += delta * (gray - mean)
    std = np.sqrt(np.maximum(0.0, m2 / max(1, len(frames))))
    mask = Image.fromarray(((std > threshold) * 255).astype(np.uint8), "L")
    mask = mask.filter(ImageFilter.MaxFilter(17))
    return np.asarray(mask, dtype=np.float32) / 255.0


def encode(frames, destination):
    container = av.open(str(destination), "w")
    stream = container.add_stream("libx264", rate=FPS)
    stream.width = WIDTH
    stream.height = HEIGHT
    stream.pix_fmt = "yuv420p"
    stream.options = {"crf": "18", "preset": "medium"}
    for image in frames:
        frame = av.VideoFrame.from_ndarray(image, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--threshold", type=float, default=2.5)
    args = parser.parse_args()

    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for beat in plan["beats"]:
        beat_id = beat["id"]
        if args.only and beat_id != args.only:
            continue
        source = H3_DIR / f"{beat_id}-h3.mp4"
        if not source.exists():
            continue
        destination = OUT_DIR / f"{beat_id}-page.mp4"
        if destination.exists() and not args.force:
            continue
        page = beat["page"]
        x, y, width, height = beat["crop"]
        base = Image.open(ROOT / "全本PPT" / "01-Comfy底图" / f"{page}-comfy底图.png").convert("RGB")
        original_crop = base.crop((x, y, x + width, y + height))
        containers = av.open(str(source))
        crops = []
        for frame in containers.decode(video=0):
            image = Image.fromarray(frame.to_ndarray(format="rgb24")).convert("RGB")
            if image.size != (width, height):
                image = image.resize((width, height), Image.Resampling.LANCZOS)
            crops.append(np.asarray(image, dtype=np.uint8))
        alpha = np.minimum(original_mask(original_crop), motion_mask(crops, args.threshold))
        alpha_image = Image.fromarray((alpha * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(6))
        alpha = np.asarray(alpha_image, dtype=np.float32)[:, :, None] / 255.0
        base_array = np.asarray(base, dtype=np.uint8)
        frames = []
        for crop in crops:
            frame = base_array.copy()
            region = frame[y:y + height, x:x + width].astype(np.float32)
            blended = region * (1.0 - alpha) + crop.astype(np.float32) * alpha
            frame[y:y + height, x:x + width] = np.clip(blended, 0, 255).astype(np.uint8)
            frames.append(frame)
        encode(frames, destination)
        print(f"{beat_id} {len(frames)}f -> {destination}", flush=True)


if __name__ == "__main__":
    main()
