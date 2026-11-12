# -*- coding: utf-8 -*-
"""验收：影片全解码 + 音视频时长 + 逐帧静止段 + 响度（LUFS/峰值）。

用法：
    python verify_film.py <video.mp4> [--freeze-thr 0.6] [--min-static 0.25]

输出（同时写 <video>.verify.json）：
    frames / duration / audio duration / 最长连续静止 / ≥阈值的静止段数 / I-LUFS / True Peak
判定：静止段（>=min_static 秒）必须为 0 段才通过。
"""
import sys, os, json, subprocess, argparse
import tempfile
import numpy as np

try:
    import av
except ImportError:
    sys.exit("需要 PyAV：pip install av")


def decode_motion(path, w=168, h=96):
    c = av.open(path)
    prev = None
    frames = 0
    diffs = []
    for f in c.decode(video=0):
        a = f.to_ndarray(format="rgb24")
        H, W, _ = a.shape
        ys = (np.arange(h) * H // h).clip(0, H - 1)
        xs = (np.arange(w) * W // w).clip(0, W - 1)
        s = a[ys][:, xs].astype(np.int16)
        if prev is not None:
            diffs.append(float(np.abs(s - prev).mean()))
        prev = s
        frames += 1
    return frames, np.array(diffs)


def stream_info(path):
    c = av.open(path)
    v = next((s for s in c.streams if s.type == "video"), None)
    a = next((s for s in c.streams if s.type == "audio"), None)
    def dur(s):
        return round(float(s.duration * s.time_base), 3) if s and s.duration else None
    return {
        "width": v.codec_context.width if v else None,
        "height": v.codec_context.height if v else None,
        "fps": str(v.average_rate) if v else None,
        "video_dur": dur(v),
        "audio_dur": dur(a),
        "audio_sr": a.codec_context.sample_rate if a else None,
    }


def loudness(path, ffmpeg):
    r = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", path,
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    import re
    i = re.findall(r"I:\s*(-?\d+\.\d+) LUFS", r.stderr)
    pk = re.findall(r"Peak:\s*(-?\d+\.\d+) dBFS", r.stderr)
    lra = re.findall(r"LRA:\s*(-?\d+\.\d+) LU", r.stderr)
    return {"i_lufs": float(i[-1]) if i else None,
            "true_peak_dbfs": float(pk[-1]) if pk else None,
            "lra_lu": float(lra[-1]) if lra else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--freeze-thr", type=float, default=0.6, help="相邻帧差阈值(0-255)")
    ap.add_argument("--min-static", type=float, default=0.25, help="判定静止的最短持续秒数")
    ap.add_argument("--fps", type=float, default=24.0)
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--out", default=None,
                    help="验收 JSON 输出路径；默认写在影片同目录，不可写时退到当前目录/临时目录")
    a = ap.parse_args()

    frames, d = decode_motion(a.video)
    info = stream_info(a.video)
    thr = a.freeze_thr
    min_frames = int(round(a.min_static * a.fps))

    runs = []
    st = None
    for i, x in enumerate(d):
        if x < thr:
            if st is None:
                st = i
        else:
            if st is not None:
                runs.append(i - st)
                st = None
    if st is not None:
        runs.append(len(d) - st)
    long_runs = [r for r in runs if r >= min_frames]

    res = {
        "file": os.path.abspath(a.video),
        "frames_decoded": frames,
        "theoretical_frames": info.get("video_dur") and round(info["video_dur"] * a.fps) or None,
        **info,
        "freeze": {
            "threshold_per_255": thr,
            "min_static_sec": a.min_static,
            "static_segments": len(long_runs),
            "static_total_sec": round(sum(long_runs) / a.fps, 2),
            "longest_static_sec": round(max(runs) / a.fps, 2) if runs else 0.0,
            "frames_below_thr_pct": round(float((d < thr).mean() * 100), 1),
        },
        "loudness": loudness(a.video, a.ffmpeg),
    }
    res["pass"] = (res["freeze"]["static_segments"] == 0
                   and abs((info.get("video_dur") or 0) - (info.get("audio_dur") or 0)) < 0.05)

    print(json.dumps(res, ensure_ascii=False, indent=2))

    # 验收结论已经出来了，写 JSON 失败绝不能把 pass 变成 fail
    candidates = []
    if a.out:
        candidates.append(a.out)
    else:
        candidates.append(os.path.splitext(a.video)[0] + ".verify.json")
    # 显式 --out 也要有退路：目标不可写时仍要留下结果
    candidates.append(os.path.join(os.getcwd(), "verify.json"))
    candidates.append(os.path.join(tempfile.gettempdir(), "verify.json"))
    written = None
    for path in candidates:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
            written = path
            break
        except Exception as e:
            print("[warn] 验收 JSON 无法写入 %s：%s（已跳过，不影响结论）" % (path, e),
                  file=sys.stderr)
    if written:
        print("[info] 验收 JSON 已写入：%s" % written, file=sys.stderr)

    sys.exit(0 if res["pass"] else 2)


if __name__ == "__main__":
    main()