# -*- coding: utf-8 -*-
"""Verify that every character beat has real local motion."""
import json
from pathlib import Path

import av
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
PLAN = SCRIPT_DIR / "character-beats.json"
BEAT_DIR = ROOT / "03-镜头分段" / "character-motion-beats"
AUDIO_REPORT = ROOT / "07-验收证据" / "beat-audio-report.json"
OUT = ROOT / "07-验收证据" / "character-motion-report.json"


def motion_stats(path, crop):
    x, y, width, height = crop
    container = av.open(str(path))
    previous = None
    diffs = []
    for frame in container.decode(video=0):
        image = frame.to_ndarray(format="rgb24")[y:y + height, x:x + width]
        step_y = max(1, height // 96)
        step_x = max(1, width // 96)
        sample = image[::step_y, ::step_x].astype(np.float32)
        if previous is not None:
            diffs.append(float(np.abs(sample - previous).mean()))
        previous = sample
    values = np.asarray(diffs, dtype=np.float32)
    flags = values < 0.35
    runs = []
    start = None
    for index, flag in enumerate(flags):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append(index - start)
            start = None
    if start is not None:
        runs.append(len(flags) - start)
    return {
        "meanDiff": round(float(values.mean()), 3),
        "p10Diff": round(float(np.percentile(values, 10)), 3),
        "maxDiff": round(float(values.max()), 3),
        "longestLocalStillSec": round(max(runs) / 24 if runs else 0, 3),
    }


def main():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))["beats"]
    audio = json.loads(AUDIO_REPORT.read_text(encoding="utf-8"))
    beats = []
    for beat in plan:
        path = BEAT_DIR / f"{beat['id']}-page.mp4"
        stats = motion_stats(path, beat["crop"])
        passed = stats["meanDiff"] >= 0.8 and stats["longestLocalStillSec"] <= 0.25
        beats.append(
            {
                "id": beat["id"],
                "page": beat["page"],
                "crop": beat["crop"],
                **stats,
                "passed": passed,
            }
        )
    report = {
        "beats": beats,
        "passed": all(item["passed"] for item in beats),
        "minMeanDiff": min(item["meanDiff"] for item in beats),
        "maxLongestStillSec": max(item["longestLocalStillSec"] for item in beats),
        "audio": {
            "total": audio["total"],
            "charsPerSec": round(
                sum(page["chars"] for page in audio["pages"]) / audio["total"], 3
            ),
            "maxInternalGapSec": max(page["maxInternalGapSec"] for page in audio["pages"]),
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "beats": len(beats),
                "minMeanDiff": report["minMeanDiff"],
                "maxLongestStillSec": report["maxLongestStillSec"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
