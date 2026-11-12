# -*- coding: utf-8 -*-
"""逐句字幕烧录 + 逐行对比度实测（WCAG）。给每镜输出 Sxx-sub.mp4，并汇报最差对比度。

输入 project.json：
{
  "shotsDir": "…/shots",              // 含 Sxx-v.mp4（已适配时长、无字幕）
  "timeline": "…/timeline.json",
  "outDir":   "…/shots",
  "ffmpeg":   "ffmpeg",
  "font":     "C:/Windows/Fonts/simkai.ttf",
  "positions": {"S01":{"x":48,"y0":656,"fs":44,"lh":64}, …},
  "contrastTarget": 4.5,
  "keywordColors": {"太阳":"#C0392B", "小鸟":"#1F4E79", "快乐":"#A11B2E"},
  "bodyColor": "#2B2B2B",
  "crf": 18
}

字幕规则：一句一条（timeline 里每 seg 一条），按 `|` 预分行，超宽自动按字号折行；
每行单独采样背景亮度并选字色（白/近黑/关键词色），保证对比度 ≥ target。

用法：python render_subs.py project.json
"""
import os, sys, json, subprocess, argparse
import numpy as np
from PIL import Image

WHITE, DARK = "#FFFFFF", "#0F0F0F"
STROKE_LIGHT, STROKE_DARK = "#1A1A1A", "#FFFFFF"


def srgb_lin(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rel_lum(rgb):
    return 0.2126 * srgb_lin(rgb[..., 0]) + 0.7152 * srgb_lin(rgb[..., 1]) + 0.0722 * srgb_lin(rgb[..., 2])


def contrast(a, b):
    la, lb = float(rel_lum(np.asarray(a, float))), float(rel_lum(np.asarray(b, float)))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hex_rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64)


def esc(t):
    return (t.replace("\\", "\\\\").replace(":", "\\:")
             .replace("'", "\u2019").replace("%", "\\%"))


def wrap(text, x, fs, width):
    per = max(6, int((width - x - 40) / fs))
    out, cur = [], ""
    for ch in text:
        if len(cur) >= per:
            out.append(cur); cur = ""
        cur += ch
    if cur:
        out.append(cur)
    return out


def grab(video, t, dst, ff):
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % t,
                    "-i", video, "-frames:v", "1", dst], capture_output=True)
    return dst if os.path.exists(dst) else None


def band_stats(png, x, y, w, fs):
    g = np.asarray(Image.open(png).convert("RGB"), dtype=np.float64)
    x1, x2 = max(0, x), min(g.shape[1], x + int(w))
    y1, y2 = max(0, y - 16), max(1, y - 6)
    band = g[y1:y2, x1:x2].reshape(-1, 3)
    if len(band) < 10:
        y1, y2 = min(g.shape[0] - 1, y + fs + 6), min(g.shape[0], y + fs + 16)
        band = g[y1:y2, x1:x2].reshape(-1, 3)
    if len(band) < 10:
        return None
    L = rel_lum(band); n = max(1, int(len(L) * 0.2))
    return band[np.argsort(L)[-n:]].mean(axis=0), band[np.argsort(L)[:n]].mean(axis=0)


def pick(bg, semantic, target):
    best = None
    candidates = [(semantic, STROKE_DARK), (DARK, STROKE_DARK), (WHITE, STROKE_LIGHT)]
    for fg, st in candidates:
        c = min(contrast(hex_rgb(fg), bg[0]), contrast(hex_rgb(fg), bg[1]))
        if best is None or c > best[2]:
            best = (fg, st, c)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8"))
    ff = cfg.get("ffmpeg", "ffmpeg")
    tl = json.load(open(cfg["timeline"], encoding="utf-8"))
    font = cfg.get("font", r"C:\Windows\Fonts\simkai.ttf").replace("\\", "/").replace(":", "\\:")
    W = int(cfg.get("width", 1344)); H = int(cfg.get("height", 768))
    target = float(cfg.get("contrastTarget", 4.5))
    kw = cfg.get("keywordColors", {})
    body = cfg.get("bodyColor", "#2B2B2B")
    tmpdir = os.path.join(cfg["outDir"], "_cv"); os.makedirs(tmpdir, exist_ok=True)

    report, worst = [], 999.0
    for s in tl["shots"]:
        sid = s["id"]
        pos = cfg["positions"][sid]
        x, y0, fs, lh = pos["x"], pos["y0"], pos["fs"], pos["lh"]
        src = os.path.join(cfg["shotsDir"], sid + "-v.mp4")
        dst = os.path.join(cfg["outDir"], sid + "-sub.mp4")
        filters = []
        overrides = {o["text"]: o for o in cfg.get("lowContrastOverride", {}).get(sid, [])}
        used_overrides = set()
        for ci, st in enumerate(s["segs"]):
            lines = []
            for part in st["text"].split("|"):
                part = part.strip()
                if part:
                    lines.extend(wrap(part, x, fs, W))
            png = grab(src, (st["t0"] + st["t1"]) / 2, os.path.join(tmpdir, "%s-%d.png" % (sid, ci)), ff)
            for li, line in enumerate(lines):
                yy = y0 + li * lh
                sem = next((c for k, c in kw.items() if k in line), body)
                ov = overrides.get(line)
                if ov:
                    if ov.get("panel"):
                        px, py, pw, ph = ov["panel"]
                        enable = "between(t,%.3f,%.3f)" % (st["t0"], st["t1"])
                        filters.append("drawbox=x=%d:y=%d:w=%d:h=%d:color=white@0.88:t=fill:enable='%s'"
                                       % (px, py, pw, ph, enable))
                    fg, stroke, c = ov.get("fg", body), ov.get("stroke", STROKE_DARK), 99.0
                    used_overrides.add(line)
                else:
                    bg = band_stats(png, x, yy, len(line) * fs * 0.98, fs) if png else None
                    fg, stroke, c = pick(bg, sem, target) if bg else (WHITE, STROKE_LIGHT, 0.0)
                report.append({"shot": sid, "text": line, "fg": fg, "contrast": round(c, 2)})
                worst = min(worst, c)
                aa, bb = st["t0"], st["t1"]
                alpha = ("if(lt(t,{a}+0.1),max(0,(t-{a})/0.1),"
                         "if(gt(t,{b}-0.1),max(0,({b}-t)/0.1),1))").format(a=aa, b=bb)
                filters.append("drawtext=fontfile='%s':text='%s':fontsize=%d:fontcolor=0x%s:"
                               "borderw=4:bordercolor=0x%s:x=%d:y=%d:alpha='%s'"
                               % (font, esc(line), fs, fg.lstrip("#"), stroke.lstrip("#"), x, yy, alpha))
        p = subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
                            "-vf", ",".join(filters) if filters else "null",
                            "-c:v", "libx264", "-preset", "medium", "-crf", str(cfg.get("crf", 18)),
                            "-pix_fmt", "yuv420p", "-r", str(cfg.get("fps", 24)), "-an", dst],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print("%s rc=%d 最差=%.2f:1" % (sid, p.returncode,
              min(r["contrast"] for r in report if r["shot"] == sid) if filters else 0), flush=True)
        if p.returncode != 0:
            print(p.stderr[-800:]); sys.exit(1)

    json.dump(report, open(os.path.join(cfg["outDir"], "contrast-report.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=2)
    low = [r for r in report if r["contrast"] < target]
    print("\n共 %d 行，最差 %.2f:1，低于 %.1f:1 的有 %d 行"
          % (len(report), worst, target, len(low)))
    for r in low:
        print("   ", r["shot"], r["text"], r["fg"], r["contrast"])
    sys.exit(0 if not low else 2)


if __name__ == "__main__":
    main()
