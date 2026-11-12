# -*- coding: utf-8 -*-
"""多音色逐句配音 → 每镜音轨 + 时间轴 + 逐句字幕（IndexTTS-2.5）。

输入 project.json：
{
  "indexTtsDir": "C:/…/index-tts",           // 含 checkpoints/ 与 .venv
  "pythonExe":    "C:/…/index-tts/.venv/Scripts/python.exe",  // 用它跑本脚本
  "outDir":       "…/.build/tts",
  "voices": {"narr":"…/旁白/prompt.wav", "frog":"…/青蛙/prompt.wav", …},
  "sentences": [
    {"shot":"S01","speaker":"narr","audioText":"小花籽找快乐","subText":"小花籽找快乐"},
    …
  ],
  "atempo": 1.40,        // 口播提速（音高不变）；1.0 表示不加速
  "gap": 0.15,           // 换人停顿
  "pad": 0.25,           // 镜尾气口
  "targetRmsDb": -18,    // 逐句响度归一目标
  "retries": 3,          // 短句停顿择优的额外生成版数（0=不做）
  "retryShots": ["S03-2","S04-2"]  // 需要择优的句（可选，缺省=全部 <=6s 的句子）
}

用法（必须用 index-tts 的 venv python）：
    <venv>/python tts_multi_voice.py project.json

产出：outDir/Sxx-n.wav（分句）、outDir/shots/Sxx.wav（整镜）、
      outDir/timeline.json、outDir/subtitles.srt
"""
import os, sys, json, wave, argparse
import numpy as np

SR = 24000
ADV = dict(do_sample=True, top_p=0.8, top_k=30, temperature=0.8,
           length_penalty=0.0, num_beams=3, repetition_penalty=10.0, max_mel_tokens=1500)


def read_wav(path, sr_out=SR):
    with wave.open(path, "rb") as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        d = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float64) / 32768.0
    if ch > 1:
        d = d.reshape(-1, ch).mean(axis=1)
    if sr != sr_out:
        t_old = np.linspace(0, len(d) / sr, len(d), endpoint=False)
        t_new = np.linspace(0, len(d) / sr, int(len(d) * sr_out / sr), endpoint=False)
        d = np.interp(t_new, t_old, d)
    return d


def write_wav(path, d, sr=SR):
    iv = np.clip(d * 32767, -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(iv.tobytes())


def max_internal_gap(d, sr, thr_db=-42, min_dur=0.08):
    """返回（最长内部静音秒数, 时长）。用于断句验收与择优。"""
    hop = int(sr * 0.01); win = int(sr * 0.03)
    thr = 10 ** (thr_db / 20.0)
    flags = [float(np.sqrt(np.mean(d[i:i + win] ** 2))) < thr
             for i in range(0, max(1, len(d) - win), hop)]
    runs = []; st = None
    for i, f in enumerate(flags):
        if f and st is None:
            st = i
        elif not f and st is not None:
            runs.append((st, i)); st = None
    if st is not None:
        runs.append((st, len(flags)))
    gs = [(b - a) * 0.01 for (a, b) in runs if a > 2 and b < len(flags) - 2]
    return (max(gs) if gs else 0.0), len(d) / float(sr)


def norm_to_target(d, target_rms_db, ceil_db=-1.0):
    """逐句 RMS 归一到目标，峰值超顶用 tanh 软限幅（硬衰减会归不到位）。"""
    rms = float(np.sqrt(np.mean(d ** 2)))
    pk = float(np.max(np.abs(d)))
    if rms < 1e-9 or pk < 1e-9:
        return d
    gain = min(10 ** ((target_rms_db - 20 * np.log10(rms)) / 20.0), 10 ** (18 / 20.0))
    ceil = 10 ** (ceil_db / 20.0)
    for _ in range(4):
        out = ceil * np.tanh(d * gain / ceil)
        r = float(np.sqrt(np.mean(out ** 2)))
        if r <= 1e-9 or abs(20 * np.log10(r) - target_rms_db) < 0.3:
            break
        gain *= 10 ** ((target_rms_db - 20 * np.log10(r)) / 20.0)
    return out


def ts(sec):
    h = int(sec // 3600); m = int((sec % 3600) // 60); s = sec % 60
    return "%02d:%02d:%02d,%03d" % (h, m, int(s), round((s - int(s)) * 1000))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8"))

    P = cfg["indexTtsDir"]
    out = cfg["outDir"]; os.makedirs(out, exist_ok=True)
    shots_dir = os.path.join(out, "shots"); os.makedirs(shots_dir, exist_ok=True)
    cache = os.path.join(out, "_cache"); os.makedirs(cache, exist_ok=True)

    # 依赖离线权重，先把缓存目录指到项目内，避免 APPDATA 权限问题
    for k in ("APPDATA", "LOCALAPPDATA", "HF_HOME", "HUGGINGFACE_HUB_CACHE",
              "TRANSFORMERS_CACHE", "MPLCONFIGDIR", "NUMBA_CACHE_DIR", "TORCH_HOME"):
        os.environ[k] = cache
    os.environ["HF_HUB_CACHE"] = os.path.join(P, "checkpoints", "hf_cache")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.chdir(P); sys.path.insert(0, P)

    from indextts.infer_v2_5 import IndexTTS2
    tts = IndexTTS2(cfg_path=os.path.join(P, "checkpoints", "config.yaml"),
                    model_dir=os.path.join(P, "checkpoints"),
                    use_bf16=False, device=cfg.get("device", "cuda:0"),
                    use_cuda_kernel=False, use_deepspeed=False, use_accel=False,
                    use_torch_compile=False, use_qwen_emo=False)
    print(">> 权重已加载", flush=True)

    atempo = float(cfg.get("atempo", 1.0))
    gap = float(cfg.get("gap", 0.15))
    pad = float(cfg.get("pad", 0.25))
    target = float(cfg.get("targetRmsDb", -18))
    retries = int(cfg.get("retries", 0))
    retry_shots = set(cfg.get("retryShots", []))
    import subprocess
    ff = cfg.get("ffmpeg", "ffmpeg")

    # 1) 逐句合成（含停顿择优）
    segs = []
    idx = {}
    for item in cfg["sentences"]:
        sid, spk = item["shot"], item["speaker"]
        n = idx.get(sid, 0); idx[sid] = n + 1
        key = "%s-%d" % (sid, n)
        s_atempo = float(item.get("atempo", atempo))
        raw = os.path.join(out, key + ".raw.wav")
        use_retry = retries > 0 and (not retry_shots or key in retry_shots)
        if not os.path.exists(raw):
            best = None
            for t in range(1 + (retries if use_retry else 0)):
                p = raw if t == 0 else os.path.join(cache, "%s-t%d.wav" % (key, t))
                tts.infer(spk_audio_prompt=cfg["voices"][spk], text=item["audioText"],
                          output_path=p, lang=cfg.get("lang", "zh"),
                          emo_audio_prompt=None, emo_alpha=1.0, interval_silence=0,
                          verbose=False, max_text_tokens_per_segment=120, **ADV)
                if not use_retry:
                    break
                g, _ = max_internal_gap(read_wav(p), SR)
                if best is None or g < best[0]:
                    best = (g, p)
                if g <= 0.3:
                    break
            if use_retry and best and best[1] != raw:
                os.replace(best[1], raw)
                print("%s 择优 gap=%.2fs" % (key, best[0]), flush=True)
        # 提速 + 归一
        sped = os.path.join(out, key + ".wav")
        if s_atempo != 1.0:
            subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", raw,
                            "-filter:a", "atempo=%.4f" % s_atempo, "-ar", str(SR), "-ac", "1",
                            "-c:a", "pcm_s16le", sped], check=True)
        else:
            subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", raw,
                            "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", sped], check=True)
        d = norm_to_target(read_wav(sped), target)
        write_wav(sped, d)
        segs.append({**item, "key": key, "wav": sped, "dur": len(d) / float(SR)})
        print("DONE %s %s %.2fs" % (key, spk, segs[-1]["dur"]), flush=True)

    # 2) 拼镜头 + 时间轴 + 字幕
    order = []
    for it in segs:
        if it["shot"] not in order:
            order.append(it["shot"])
    shots, srt, srt_i, t0 = [], [], 1, 0.0
    for sid in order:
        mine = [it for it in segs if it["shot"] == sid]
        pieces, seg_times, cur = [], [], 0.0
        for j, it in enumerate(mine):
            if j > 0:
                pieces.append(np.zeros(int(gap * SR))); cur += gap
            d = read_wav(it["wav"])
            pieces.append(d)
            seg_times.append({"t0": cur, "t1": cur + len(d) / float(SR),
                              "text": it["subText"], "speaker": it["speaker"]})
            cur += len(d) / float(SR)
        body = np.concatenate(pieces + [np.zeros(int(pad * SR))]) if pieces else np.zeros(1)
        write_wav(os.path.join(shots_dir, sid + ".wav"), body)
        dur = len(body) / float(SR)

        for st in seg_times:
            parts = [p for p in st["text"].split("|") if p]
            span = st["t1"] - st["t0"]
            total_chars = sum(len(p) for p in parts) or 1
            c = st["t0"]
            for p in parts:
                dlen = span * len(p) / total_chars
                srt.append((srt_i, t0 + c, t0 + c + dlen, p)); srt_i += 1
                c += dlen
            st["text"] = st["text"].replace("|", "")
        shots.append({"id": sid, "start": round(t0, 4), "end": round(t0 + dur, 4),
                      "slot": round(dur, 4), "segs": seg_times})
        t0 += dur

    json.dump({"shots": shots, "total": round(t0, 4), "sr": SR,
               "atempo": atempo, "gap": gap, "pad": pad},
              open(os.path.join(out, "timeline.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    with open(os.path.join(out, "subtitles.srt"), "w", encoding="utf-8") as f:
        for i, a1, b1, txt in srt:
            f.write("%d\n%s --> %s\n%s\n\n" % (i, ts(a1), ts(b1), txt))
    print("TOTAL = %.2fs  句数=%d  字幕条=%d" % (t0, len(segs), len(srt)))


if __name__ == "__main__":
    main()
