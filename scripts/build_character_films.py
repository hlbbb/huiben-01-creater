# -*- coding: utf-8 -*-
"""Build full-page films from character-crop H3 beats."""
import json
import hashlib
import math
import subprocess
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
FFMPEG = Path(r"C:\Users\Zyz\Documents\projects\ComfyUI\custom_nodes\ComfyUI-DLSS5-Enhancer\ffmpeg\bin\ffmpeg.exe")
FFPROBE = FFMPEG.with_name("ffprobe.exe")
PLAN = SCRIPT_DIR / "character-beats.json"
TIMELINE = ROOT / "04-字幕与文案" / "page-timeline-beats.json"
BEAT_DIR = ROOT / "03-镜头分段" / "character-motion-beats"
CLIP_DIR = ROOT / "03-镜头分段" / "character-beat-clips-v2"
OUT_DIR = ROOT / "03-镜头分段" / "character-final"
TEXT_DIR = ROOT / "04-字幕与文案" / "全文叠字"
BEAT_PLAN = ROOT / "04-字幕与文案" / "character-beat-plan.json"
FPS = 24
WIDTH = 1344
HEIGHT = 768


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2200:])


def probe_duration(path):
    result = subprocess.run(
        [str(FFPROBE), "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return float(result.stdout.strip())


def allocate_frames(beats, total_frames):
    weights = [float(beat.get("weight", 1)) for beat in beats]
    total_weight = sum(weights)
    frames = [max(1, int(round(total_frames * weight / total_weight))) for weight in weights]
    frames[-1] += total_frames - sum(frames)
    return frames


def build_beat_clip(beat, target_frames, destination):
    source = BEAT_DIR / f"{beat['id']}-page.mp4"
    if not source.exists():
        raise FileNotFoundError(source)
    source_duration = probe_duration(source)
    target_duration = target_frames / FPS
    filters = []
    if source_duration + 0.02 < target_duration:
        factor = (target_duration + 2 / FPS) / source_duration
        if factor >= 1.45:
            filters.append("minterpolate=fps=48:mi_mode=mci:mc_mode=aobmc:vsbmc=1")
        filters.append(f"setpts={factor:.8f}*PTS")
    filters.extend(
        [
            "fps=24",
            f"trim=end_frame={target_frames}",
            "setpts=PTS-STARTPTS",
            f"scale={WIDTH}:{HEIGHT}:flags=lanczos",
            "setsar=1",
        ]
    )
    phase = (int(hashlib.md5(beat["id"].encode("utf-8")).hexdigest()[:4], 16) / 65535.0) * 2 * math.pi
    filters.append(f"eq=brightness=0.02*sin(2*PI*0.9*t+{phase:.4f}):eval=frame")
    run(
        [
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source),
            "-vf", ",".join(filters),
            "-frames:v", str(target_frames),
            "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-r", "24", str(destination),
        ]
    )


def overlay_text(concat_path, text_path, slot, frames, destination):
    if text_path.exists():
        command = [
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_path),
            "-loop", "1", "-i", str(text_path),
            "-filter_complex",
            f"[0:v]fps=24,trim=end_frame={frames},setpts=PTS-STARTPTS[v0];"
            f"[1:v]format=rgba[text];"
            f"[v0][text]overlay=0:0:format=auto,noise=alls=2:allf=t+u,format=yuv420p[v]",
        ]
    else:
        command = [
            str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_path),
            "-filter_complex",
            f"[0:v]fps=24,trim=end_frame={frames},setpts=PTS-STARTPTS,noise=alls=2:allf=t+u,format=yuv420p[v]",
        ]
    command += [
        "-map", "[v]", "-an", "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", "24",
        "-t", f"{slot:.6f}", str(destination),
    ]
    run(command)


def main():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))["beats"]
    timeline = json.loads(TIMELINE.read_text(encoding="utf-8"))
    by_page = defaultdict(list)
    for beat in plan:
        by_page[beat["page"]].append(beat)
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    page_clips = []
    report = []
    for shot in timeline["shots"]:
        page_id = shot["id"]
        beats = by_page.get(page_id, [])
        if not beats:
            raise RuntimeError(f"No character beats for {page_id}")
        slot = float(shot["slot"])
        total_frames = max(1, int(round(slot * FPS)))
        frames_per_beat = [total_frames] if len(beats) == 1 else allocate_frames(beats, total_frames)
        clips = []
        rows = []
        for beat, frames in zip(beats, frames_per_beat):
            target = CLIP_DIR / f"{beat['id']}-{frames}f.mp4"
            if not target.exists():
                build_beat_clip(beat, frames, target)
            clips.append(target)
            rows.append(
                {
                    "id": beat["id"],
                    "frames": frames,
                    "seconds": round(frames / FPS, 3),
                    "sourceSeconds": round(probe_duration(BEAT_DIR / f"{beat['id']}-page.mp4"), 3),
                }
            )
            print(f"{beat['id']} {frames}f {frames / FPS:.2f}s", flush=True)
        concat_path = CLIP_DIR / f"{page_id}-concat.txt"
        concat_path.write_text("\n".join(f"file '{clip.as_posix()}'" for clip in clips) + "\n", encoding="utf-8")
        output = OUT_DIR / f"{page_id}-page.mp4"
        overlay_text(concat_path, TEXT_DIR / f"{page_id}-text.png", slot, total_frames, output)
        page_clips.append(output)
        report.append({"page": page_id, "slot": round(slot, 3), "frames": total_frames, "beats": rows})
        print(f"PAGE {page_id} {slot:.2f}s -> {output}", flush=True)
    concat_path = OUT_DIR / "page-concat.txt"
    concat_path.write_text("\n".join(f"file '{clip.as_posix()}'" for clip in page_clips) + "\n", encoding="utf-8")
    BEAT_PLAN.write_text(
        json.dumps(
            {
                "pages": report,
                "beats": len(plan),
                "maxStretch": max(
                    row["seconds"] / row["sourceSeconds"] for page in report for row in page["beats"]
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"CONCAT {concat_path}")


if __name__ == "__main__":
    main()
