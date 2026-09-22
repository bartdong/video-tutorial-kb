---
name: video-tutorial-kb
description: 把视频教程(B站/YouTube 等)转成可问答的本地知识库:yt-dlp 下载 + ffmpeg 抽帧 + tesseract OCR 密度分级 + whisper 本地转写(带时间码分段) + 术语锚点 + KB.md 索引。当用户想「学习某个视频教程」「把视频做成知识库」「看视频遇到问题回头问」时使用此技能。产出支持基于视频原始内容的定位式问答(引用时间码),而非凭空泛谈。
agent_created: true
---

# 视频教程转知识库(video-tutorial-kb)

把「看视频学习」变成「视频 + 一个随时可问的领读人」。核心理念:**LLM 不搬运视频字节,只做理解与问答;所有原始素材(帧图/转写稿)落盘,回答永远锚定视频实际内容**。

## 触发场景

- 用户提供视频链接,说「学习这个」「做成知识库」「之后我要边看边问你」
- 用户已在知识库范围内的视频上提问(走问答协议,不重新处理)

## 流程总览

```
单集处理(脚本化,零 token):  下载 → 抽帧 → OCR 密度 → whisper 转写 → 分段+错字修正
理解建库(读素材,少量 token):  通读分段稿 → 提炼大纲/术语锚点 → 建 KB.md + 学习指南
按需问答(问答协议):          定位 → 调原始素材 → 扩展解释 → 关联
```

学习指南与知识库**同步生成**:KB.md 给 agent 做问答索引,学习指南(`guides/指南-PN-*.html`)给用户做预习+看视频时对照。格式规范与 HTML 模板见 `references/guide-format.md` 和 `assets/guide-template.html`。

## 一、首次部署检查(每台机器一次)

环境要求:ffmpeg、tesseract(含 chi_sim 语言包)、Python 3。

**一条命令搞定**(自动检查依赖 → 编译 whisper-cli → 下模型):

```bash
./setup.sh                              # 默认 small 模型(487MB)
WHISPER_MODEL_SIZE=base ./setup.sh      # 磁盘紧张时换 base(142MB,精度降)
```

若需手工执行(或 setup 失败时排查),步骤如下:

1. **whisper-cli 编译**(若 `scripts/whisper-cli` 不存在):
   - 源码克隆到任意临时目录: `git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git`
   - **本机已知坑(macOS CLT 环境)**:clang++ 找不到 C++ 标准库头(cstdio/mutex),`SDKROOT`/`CMAKE_OSX_SYSROOT` 均无效,必须:
     ```
     cmake -B build -DCMAKE_BUILD_TYPE=Release \
       -DCMAKE_CXX_FLAGS="-isystem $(xcrun --show-sdk-path)/usr/include/c++/v1 -isystem $(xcrun --show-sdk-path)/usr/include"
     make -C build -j 8 whisper-cli
     ```
   - 产物 `build/bin/whisper-cli` 复制到本 skill 的 `scripts/` 下
   - 无 cmake 时用 pip 隔离安装: `~/.workbuddy/binaries/python/envs/default/bin/pip install cmake`
   - brew 安装 whisper-cpp 在沙箱环境会被 /opt/homebrew 只读拦截,直接走源码编译
2. **模型下载**(若 `assets/ggml-small.bin` 不存在):
   - `https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin`(487MB)
   - 放到本 skill 的 `assets/` 下(跨项目复用)
3. 验证: `python3 scripts/process_episode.py` 不带参数运行,应打印用法而非崩溃

> `scripts/whisper-cli` 与 `assets/ggml-*.bin` 是构建产物,已 gitignore,换机器请用 `setup.sh` 重新生成。

## 二、单集处理(脚本化)

```bash
python3 ~/.workbuddy/skills/video-tutorial-kb/scripts/process_episode.py <工作目录> <P编号> <视频URL>
# 例: .../process_episode.py ~/Documents/WorkBuddy/xxx/video-pipeline 2 "https://www.bilibili.com/video/BV1NCgVzoEG9?p=2"
```

脚本自动完成:下载 480p → 6s 间隔抽帧 → tesseract 逐帧密度打分 → 提音轨 → whisper 转写 → SRT 解析为 ~45s 分段(带时间码 + 同音错字修正)。

产出落盘在 `<工作目录>/pN/`:`pN_segments.json`(分段转写)、`pN_audio.wav.srt`(原始字幕)、`frames/`(帧图)、`density_report.json`(帧密度)。

**实测性能参考(M1 Pro 16GB)**:9.5 分钟视频 = 下载 2s + 抽帧 90s + OCR 90s + 转写 60s(small 模型 Metal 加速)。全程零 API token。

**转写质量**:中文口播 small 模型只有同音错字(寒树→函数),`scripts/process_episode.py` 内置 20 条修正映射;换新 UP 主后通读一遍分段稿,把新口癖错字加进 FIXES 字典。

## 三、理解建库(LLM 步骤)

脚本跑完后:

1. **通读** `pN_segments.json` 全文(一段段读,或一次读入)
2. **建大纲**:每 2–4 分钟一个主题块,写「时间段 | 主题 | 关键概念」表
3. **术语锚点**:识别本集首次引入的核心概念(一般 5–15 个),在分段稿中定位首次出现时间,写入 `pN_term_anchors.json`(格式:`[{"term": "激活函数", "seg": 6, "t": 277.5}]`)
4. **更新 KB.md**(工作目录根):系列名、目录结构、各集大纲表、术语锚点汇总、素材路径
5. **生成学习指南**:按 `references/guide-format.md` 的六段结构,基于 `assets/guide-template.html` 模板填充,输出 `<工作目录>/guides/指南-PN-<集标题>.html`;多集时同步更新系列导航条和 `guides/index.html`;生成后跑 HTML 标签配对校验

## 四、按需问答

用户回来说「XX 没看懂」时,加载 `references/qa-protocol.md` 按协议回答。核心:先引用 UP 主原话(带时间码),再展开原理,最后做前后集关联。

## 关键经验(踩坑记录)

- B 站视频场景检测(`select='gt(scene,X)'`)对 av1 编码失效(0 帧输出),用 `fps=1/6` 定时抽帧替代
- 未登录 B 站 API 不给 CC 字幕,别浪费时间找字幕旁路,直接本地转写
- 旁白语速密度对「全程口播型」UP 主区分度低(4.9–5.5字/s 方差小);**术语首次出现时间是更强的抓帧/定位信号**
- 480p 对 OCR 板书公式够用(手写体识别率约 60%),公式精确结构回答时以转写稿为准
- 多 P 视频逐 P 处理,URL 带 `?p=N` 参数;whisper 模型和引擎放 skill 目录下,跨项目零重复下载
