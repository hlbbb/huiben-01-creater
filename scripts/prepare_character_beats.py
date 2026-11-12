# -*- coding: utf-8 -*-
"""Crop character regions and build the H3 character-animation config."""
import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
PLAN = SCRIPT_DIR / "character-beats.json"
CROP_DIR = ROOT / "03-镜头分段" / "character-crops"
H3_CONFIG = SCRIPT_DIR / "character-h3.json"


def main():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    CROP_DIR.mkdir(parents=True, exist_ok=True)
    shots = []
    for index, beat in enumerate(plan["beats"]):
        page = beat["page"]
        source = ROOT / "全本PPT" / "01-Comfy底图" / f"{page}-comfy底图.png"
        image = Image.open(source).convert("RGB")
        x, y, width, height = beat["crop"]
        crop_path = CROP_DIR / f"{beat['id']}.png"
        image.crop((x, y, x + width, y + height)).save(crop_path, "PNG")
        shots.append(
            {
                "id": beat["id"],
                "ref": str(crop_path.relative_to(ROOT)),
                "seconds": beat.get("seconds", 3),
                "size": beat["size"],
                "ratio": beat["ratio"],
                "prompt": beat["prompt"],
                "params": {
                    "frameMode": "first",
                    "steps": 8,
                    "cfg": 1,
                    "seed": 2026110900 + index,
                },
            }
        )
    H3_CONFIG.write_text(
        json.dumps({"outputDir": "03-镜头分段/character-h3", "shots": shots}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"beats": len(shots), "config": str(H3_CONFIG)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
