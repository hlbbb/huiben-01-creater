# -*- coding: utf-8 -*-
"""把 TTS timeline 的实测时长回填到逐镜时长规划，并给出语速门禁。"""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("plan")
    parser.add_argument("timeline")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    plan_path = Path(args.plan).resolve()
    timeline_path = Path(args.timeline).resolve()
    output_path = Path(args.out).resolve() if args.out else plan_path
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    shots = {shot["id"]: shot for shot in timeline.get("shots", [])}
    gap = plan["assumptions"]["gapSec"]
    pad = plan["assumptions"]["padSec"]
    max_atempo = plan["assumptions"]["maxNaturalAtempo"]

    issues = []
    for page in plan["pages"]:
        shot = shots.get(page["id"])
        if not shot:
            issues.append(f"missing TTS timeline shot: {page['id']}")
            continue
        slot = float(shot["slot"])
        measured = max(0.0, slot - gap - pad)
        available_speech = page["plannedSlotSec"] - gap - pad
        required_atempo = measured / max(available_speech, 0.01)
        page.update(
            {
                "measuredAudioSec": round(measured, 3),
                "actualSlotSec": round(slot, 3),
                "requiredAtempo": round(required_atempo, 3),
                "status": "measured",
            }
        )
        if required_atempo > max_atempo + 1e-6:
            issues.append(
                f"{page['id']}: requires atempo {required_atempo:.2f} > {max_atempo}; "
                "lengthen the shot or split the page"
            )
        if page.get("needsSplit") and page.get("splitConfirmed") is not True and page.get("extendedShotConfirmed") is not True:
            issues.append(
                f"{page['id']}: unresolved needsSplit; split the page or set "
                "extendedShotConfirmed=true after user approval"
            )

    plan["check"] = {
        "passed": not issues,
        "issues": issues,
        "maxObservedAtempo": max(
            (page.get("requiredAtempo", 0) for page in plan["pages"]), default=0
        ),
        "measuredFrom": str(timeline_path),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"saved {output_path}: passed={plan['check']['passed']} "
        f"maxAtempo={plan['check']['maxObservedAtempo']:.2f}"
    )
    for issue in issues:
        print("ISSUE", issue)
    if issues:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
