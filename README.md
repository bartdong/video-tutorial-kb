# video-tutorial-kb

把视频教程（B站 / YouTube 等）转成**可问答的本地知识库**：看完能查，忘了能问，问了能定位到视频的哪一秒。

核心理念：**LLM 不搬运视频字节，只做理解与问答**。所有原始素材（帧图 / 转写稿 / OCR 密度）落盘，回答永远锚定视频实际内容并引用时间码，而不是凭空泛谈。

## 它做什么

```
单集处理（脚本化，零 token）   下载 → 抽帧 → OCR 密度 → whisper 本地转写 → 分段 + 错字修正
理解建库（读素材，少量 token） 通读分段稿 → 提炼大纲 / 术语锚点 → 建 KB.md + 学习指南
按需问答（问答协议）           定位 → 调原始素材 → 扩展解释 → 前后集关联
```

产出两样东西，同步生成：

- `KB.md` —— 给 agent 用的问答索引（大纲 / 术语锚点 / 素材路径）
- `guides/指南-PN-*.html` —— 给人看的**学习指南**：看前 5 分钟预习建立概念地图，看视频时随时翻查对照

## 安装

```bash
git clone https://github.com/bartdong/video-tutorial-kb.git \
  ~/.workbuddy/skills/video-tutorial-kb
cd ~/.workbuddy/skills/video-tutorial-kb
./setup.sh
```

`setup.sh` 会：

1. 检查 `ffmpeg` / `ffprobe` / `tesseract`（含 `chi_sim` 语言包）/ `yt-dlp`
2. 编译 `whisper.cpp` 的 `whisper-cli` 到 `scripts/`（源码现拉现编，产物不入库）
3. 下载 whisper 模型到 `assets/`（默认 `ggml-small.bin`，487MB；磁盘紧张可用 `ggml-base.bin`）

> macOS 源码编译有个坑：CLT 环境下 `clang++` 找不到 C++ 标准库头，`SDKROOT` / `CMAKE_OSX_SYSROOT` 都无效，
> 必须显式加 `-isystem $(xcrun --show-sdk-path)/usr/include/c++/v1`。`setup.sh` 已内置。

## 用法

```bash
python3 scripts/process_episode.py <工作目录> <P编号> <视频URL>
# 例
python3 scripts/process_episode.py ~/video-pipeline 2 \
  "https://www.bilibili.com/video/BV1NCgVzoEG9?p=2"
```

产出落在 `<工作目录>/pN/`：

| 文件 | 说明 |
| --- | --- |
| `pN_segments.json` | 带时间码的分段转写稿（约 45s / 段） |
| `pN_term_anchors.json` | 术语首次出现的时间锚点 |
| `pN_audio.wav.srt` | whisper 原始字幕 |
| `frames/t_XXXX.jpg` | 6 秒间隔抽帧 |
| `density_report.json` | 每帧 OCR 信息密度 |

脚本跑完后交给 agent 做「理解建库」（通读分段稿 → 大纲 → 术语锚点 → KB.md → 学习指南）；
之后你回来问「XX 没看懂」，它按 `references/qa-protocol.md` 引用原话 + 时间码回答。

### 实测性能（M1 Pro 16GB）

9.5 分钟视频：下载 2s + 抽帧 90s + OCR 90s + 转写 60s（small 模型，Metal 加速）。**全程零 API token**。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WHISPER_CLI` | `scripts/whisper-cli` | whisper 可执行文件路径 |
| `WHISPER_MODEL` | `assets/ggml-small.bin` | ggml 模型路径 |
| `YT_DLP` | 自动探测 | yt-dlp 路径，找不到则退回 PATH 里的 `yt-dlp` |

## 目录结构

```
SKILL.md                  技能主文档（含踩坑记录）
setup.sh                  依赖检查 + whisper-cli 编译 + 模型下载
scripts/
  process_episode.py      单集处理流水线
references/
  guide-format.md         学习指南六段结构规范
  qa-protocol.md          按需问答协议
assets/
  guide-template.html     学习指南 HTML 模板（{{占位符}}）
```

`assets/ggml-*.bin` 与 `scripts/whisper-cli` 是构建产物，已 gitignore，请用 `setup.sh` 生成。

## 已知坑

- B 站 av1 编码下场景检测（`select='gt(scene,X)'`）失效、输出 0 帧 → 用 `fps=1/6` 定时抽帧替代
- 未登录时 B 站 API 不给 CC 字幕 → 别找字幕旁路，直接本地转写
- 中文口播 small 模型只错同音字（寒树→函数），`FIXES` 字典已内置 20 条；换 UP 主后通读一遍分段稿补新口癖
- 旁白语速密度对「全程口播型」UP 主区分度低 → **术语首次出现时间是更强的定位信号**
- 480p 对 OCR 板书够用（手写体识别率约 60%），公式精确结构以转写稿为准
