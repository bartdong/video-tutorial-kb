#!/usr/bin/env python3
"""视频教程 → 知识库流水线 · 单集处理脚本

用法:
  python3 process_episode.py <工作目录> <P编号> <视频URL> [--kb-title "系列标题"]

前置依赖(缺失时脚本会明确报错):
  - ffmpeg/ffprobe 在 PATH
  - tesseract 带 chi_sim 语言包
  - whisper-cli 已编译(路径见 WHISPER_CLI 常量,可通过环境变量 WHISPER_CLI 覆盖)
  - whisper ggml 模型(路径见 WHISPER_MODEL 常量,可通过环境变量 WHISPER_MODEL 覆盖)

产出(写入 <工作目录>/pN/):
  pN.mp4 / pN_audio.wav / pN_audio.wav.srt / pN_audio.wav.txt
  pN_segments.json      带时间码分段转写稿(~45s/段)
  pN_term_anchors.json  术语首次出现时间锚点
  frames/t_XXXX.jpg     全部抽帧(6s间隔)
  density_report.json   每帧 OCR 信息密度
"""
import subprocess, sys, re, json, os
from pathlib import Path

# 资源路径:优先环境变量,其次相对本脚本位置解析(便于仓库整体搬到任意目录)
SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = os.environ.get("WHISPER_MODEL_SIZE", "small")  # small | base | tiny ...

WHISPER_CLI = os.environ.get("WHISPER_CLI", str(SKILL_ROOT / "scripts/whisper-cli"))
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", str(SKILL_ROOT / f"assets/ggml-{DEFAULT_MODEL}.bin"))

# 常见口播同音错字修正表(中文技术口播为主;遇到新 UP 主按口癖增补)
FIXES = {
    "寒树": "函数", "Deepseeker": "DeepSeek", "累白烂了": "摆烂了",
    "联盟代猜": "连蒙带猜", "金四解": "近似解", "现行变换": "线性变换",
    "非现行": "非线性", "线性寒树": "线性函数", "激活寒树": "激活函数",
    "神经园": "神经元", "数物": "输入", "在者呢": "再者呢",
    "直角三有形": "直角三角形", "史诗技难题": "史诗级难题",
    "再次基础直上": "在此基础上", "71毛钱关系": "没有半毛钱关系",
}


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print(f"[FAIL] {' '.join(cmd[:4])}...\n{r.stderr[-800:]}", file=sys.stderr)
        sys.exit(1)
    return r


def find_ytdlp() -> str:
    """定位 yt-dlp:PATH → 受管 venv → 报错提示安装方式。"""
    import shutil
    found = shutil.which("yt-dlp")
    if found:
        return found
    venv = Path.home() / ".workbuddy/binaries/python/envs/default/bin/yt-dlp"
    if venv.exists():
        return str(venv)
    print("[缺少 yt-dlp] 安装: pip install yt-dlp  "
          "(或 brew install yt-dlp),也可用 YT_DLP 环境变量指定路径", file=sys.stderr)
    sys.exit(1)


def ts2s(t: str) -> float:
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    workdir, pnum, url = Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3]
    ep_dir = workdir / f"p{pnum}"
    ep_dir.mkdir(parents=True, exist_ok=True)

    for tool, hint in [
        ("ffmpeg", "brew install ffmpeg"),
        ("ffprobe", "brew install ffmpeg"),
        ("tesseract", "brew install tesseract && 下载 chi_sim 语言包"),
    ]:
        if subprocess.run(["which", tool], capture_output=True).returncode != 0:
            print(f"[缺少工具] {tool} → {hint}", file=sys.stderr)
            sys.exit(1)
    if not Path(WHISPER_CLI).exists():
        print(f"[缺少 whisper-cli] 预期位置: {WHISPER_CLI}\n"
              f"编译方法见 SKILL.md「首次部署」节", file=sys.stderr)
        sys.exit(1)
    if not Path(WHISPER_MODEL).exists():
        print(f"[缺少模型] 预期位置: {WHISPER_MODEL}\n"
              f"下载: https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin", file=sys.stderr)
        sys.exit(1)

    # 1. 下载(480p 足够 OCR/听清,B 站场景检测对 av1 无效用定时抽帧)
    mp4 = ep_dir / f"p{pnum}.mp4"
    if not mp4.exists():
        ytdlp = os.environ.get("YT_DLP") or find_ytdlp()
        run([ytdlp, "-f", "bv*[height<=480][ext=mp4]+ba[ext=m4a]/b[height<=480][ext=mp4]/b",
             "--merge-output-format", "mp4", "-o", str(mp4), url])

    # 2. 抽帧(6s 间隔;480p 下 q:v 5 平衡体积与 OCR 精度)
    frames_dir = ep_dir / "frames"
    frames_dir.mkdir(exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4),
         "-vf", "fps=1/6,scale=852:-1", "-q:v", "5", str(frames_dir / "t_%04d.jpg")])

    # 3. OCR 信息密度
    density = []
    for img in sorted(frames_dir.glob("t_*.jpg")):
        idx = int(re.search(r"(\d+)", img.stem).group(1))
        out = run(["tesseract", str(img), "stdout", "-l", "chi_sim+eng", "--psm", "3"],
                  timeout=60).stdout
        flat = re.sub(r"\s+", "", out)
        cjk = sum(1 for c in flat if "\u4e00" <= c <= "\u9fff")
        latin = len([w for w in re.split(r"[^A-Za-z0-9_+\-=]+", out) if len(w) >= 2])
        density.append({"idx": idx, "ts": (idx - 1) * 6.0, "density": cjk + latin,
                        "cjk": cjk, "latin": latin, "preview": flat[:40]})
    (ep_dir / "density_report.json").write_text(json.dumps(density, ensure_ascii=False, indent=1))

    # 4. 提音轨 + whisper 转写(产出 srt + txt)
    wav = ep_dir / f"p{pnum}_audio.wav"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    run([WHISPER_CLI, "-m", WHISPER_MODEL, "-f", str(wav), "-l", "zh",
         "--threads", "8", "--output-txt", "--output-srt", "--print-progress", "false"],
        cwd=ep_dir)

    # 5. SRT → 分段转写稿(带时间码 + 错字修正)
    srt = (ep_dir / f"p{pnum}_audio.wav.srt").read_text()
    blocks = re.findall(
        r"(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\Z)",
        srt, re.S)
    segs, cur, start, end, si = [], [], None, None, 0
    for _, s0, e0, txt in blocks:
        for k, v in FIXES.items():
            txt = txt.replace(k, v)
        txt = txt.strip()
        if not txt:
            continue
        s, e = ts2s(s0), ts2s(e0)
        if start is None:
            start = s
        if e - start > 45 and cur:
            segs.append({"seg": si, "start": round(start, 1), "end": round(end, 1),
                         "text": "".join(cur)})
            si += 1
            cur, start = [], e
        cur.append(txt)
        end = e
    if cur:
        segs.append({"seg": si, "start": round(start, 1), "end": round(end, 1),
                     "text": "".join(cur)})
    (ep_dir / f"p{pnum}_segments.json").write_text(json.dumps(segs, ensure_ascii=False, indent=1))

    print(f"[完成] p{pnum}: {len(segs)} 段 | 帧密度报告 {len(density)} 帧 | {mp4}")
    print(f"下一步: ① 通读 {ep_dir}/p{pnum}_segments.json 提炼大纲与术语 → ② 建 KB.md → ③ 计算 p{pnum}_term_anchors.json")


if __name__ == "__main__":
    main()
