# -*- coding: utf-8 -*-
"""把原始镜头按时间轴槽位适配成成片镜头：运动插帧 + 匀速拉伸（零冻结），
对运动量小的镜头叠一层缓慢推镜（zoompan），避免"插帧后更呆"。

输入 project.json：
{
  "srcDir":  "…/03-镜头分段",          // 原始镜头 mp4，文件名前 3 字符是镜号（S01…）
  "outDir":  "…/shots",                // 输出 Sxx-v.mp4
  "timeline":"…/timeline.json",        // {"shots":[{"id":"S01","slot":3.2}, …]}
  "ffmpeg":  "ffmpeg",
  "width": 1344, "height": 768, "fps": 24,
  "zoomShots": ["S04","S06","S08"],    // 额外强制加缓推的镜号
  "zoomFromSlow": 1.9,                 // 拉伸比 >= 该值的镜头自动加缓推
  "zoomAmount": 0.05,                  // 1.00 -> 1+zoomAmount
  "crf": 18
}

用法：python fit_shots.py project.json [--force]
"""
import os, sys, json, glob, re, subprocess, argparse


def dur_of(path, ffmpeg):
    r = subprocess.run([ffmpeg, "-hide_banner", "-i", path], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
    if not m:
        raise SystemExit("无法读取时长: " + path)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--force", action="store_true", help="已存在的输出也重渲")
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8"))

    ff = cfg.get("ffmpeg", "ffmpeg")
    fps = float(cfg.get("fps", 24))
    W = int(cfg.get("width", 1344))
    H = int(cfg.get("height", 768))
    crf = int(cfg.get("crf", 18))
    zoom_amt = float(cfg.get("zoomAmount", 0.05))
    from_slow = float(cfg.get("zoomFromSlow", 1.9))
    zoom_shots = set(cfg.get("zoomShots", []))
    os.makedirs(cfg["outDir"], exist_ok=True)

    srcmap = {os.path.basename(p)[:3]: p for p in glob.glob(os.path.join(cfg["srcDir"], "*.mp4"))}
    tl = json.load(open(cfg["timeline"], encoding="utf-8"))

    ok = True
    for s in tl["shots"]:
        sid = s["id"]
        slot = float(s["slot"])
        src = srcmap.get(sid)
        if not src:
            print("缺少源镜头:", sid)
            ok = False
            break

        dst = os.path.join(cfg["outDir"], sid + "-v.mp4")
        if os.path.exists(dst) and os.path.getsize(dst) > 20000 and not a.force:
            print("SKIP", sid)
            continue

        dur_src = dur_of(src, ff)
        R = slot / dur_src
        need = int(round(slot * fps))
        interp_fps = fps if R <= 1.02 else int(min(120, max(25, R * fps * 1.06)))

        vf = []
        if R > 1.02:
            vf.append("minterpolate=fps=%d:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=0"
                      % interp_fps)
        vf.append("setpts=%.6f*PTS" % R)
        vf.append("fps=%d" % fps)

        do_zoom = (R >= from_slow) or (sid in zoom_shots)
        if do_zoom:
            up_w, up_h = int(W * 1.0714), int(H * 1.0714)
            vf.append("scale=%d:%d" % (up_w, up_h))
            vf.append("zoompan=z='1+%.4f*on/%d':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':"
                      "d=1:s=%dx%d:fps=%d" % (zoom_amt, max(1, need - 1), W, H, fps))

        tmp = os.path.join(cfg["outDir"], sid + "-v.tmp.mp4")
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
               "-vf", ",".join(vf), "-frames:v", str(need), "-an",
               "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
               "-pix_fmt", "yuv420p", "-r", str(int(fps)), tmp]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            print("FAIL", sid, p.stderr[-800:])
            ok = False
            break
        os.replace(tmp, dst)   # 原子覆盖，避免半成品
        print("%s R=%.3f interp=%.0ffps frames=%d zoom=%s OK"
              % (sid, R, interp_fps, need, do_zoom))

    print("ALL_OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()