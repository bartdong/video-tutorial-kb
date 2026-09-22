#!/usr/bin/env bash
# video-tutorial-kb 首次部署:检查依赖 → 编译 whisper-cli → 下载 whisper 模型
#
# 用法:
#   ./setup.sh                    # 默认 small 模型
#   WHISPER_MODEL_SIZE=base ./setup.sh   # 磁盘紧张时换 base(142MB)
#
# 大文件(whisper-cli 二进制 / ggml 模型)不入库,由本脚本在本机生成。
set -euo pipefail

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_SIZE="${WHISPER_MODEL_SIZE:-small}"
JOBS="$(sysctl -n hw.ncpu 2>/dev/null || echo 8)"

case "$MODEL_SIZE" in
  tiny|base|small|medium|large) ;;
  *) echo "[错误] 未知模型: $MODEL_SIZE (可选 tiny|base|small|medium|large)" >&2; exit 1 ;;
esac

cd "$SKILL_ROOT"
mkdir -p scripts assets

echo "==> 1/3 检查系统依赖"
MISSING=0
for tool in ffmpeg ffprobe tesseract; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '    %-10s OK\n' "$tool"
  else
    printf '    %-10s 缺失 → brew install %s\n' "$tool" "${tool/ffprobe/ffmpeg}"
    MISSING=1
  fi
done

# tesseract 中文语言包(chi_sim 是 OCR 板书的前提;缺失则自动下载到 tessdata)
if command -v tesseract >/dev/null 2>&1; then
  if tesseract --list-langs 2>/dev/null | grep -qx "chi_sim"; then
    echo "    chi_sim     OK"
  else
    TESSDATA_DIR="$(tesseract --list-langs 2>&1 | sed -n 's/.*"\(.*tessdata[^"]*\)".*/\1/p' | head -1)"
    if [ -z "$TESSDATA_DIR" ] || [ ! -d "$TESSDATA_DIR" ]; then
      TESSDATA_DIR="$(brew --prefix 2>/dev/null)/share/tessdata"
    fi
    echo "    chi_sim     缺失 → 下载 chi_sim.traineddata 到 $TESSDATA_DIR"
    if [ -d "$TESSDATA_DIR" ] && [ -w "$TESSDATA_DIR" ]; then
      # 直连 raw.githubusercontent 在国内常超时,优先走 gh-proxy 镜像,失败再回退直连
      curl -fL --connect-timeout 15 --max-time 180 \
        "https://gh-proxy.com/https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/chi_sim.traineddata" \
        -o "$TESSDATA_DIR/chi_sim.traineddata" \
        || curl -fL --connect-timeout 15 --max-time 180 \
          "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/chi_sim.traineddata" \
          -o "$TESSDATA_DIR/chi_sim.traineddata"
      echo "    chi_sim     已下载"
    else
      echo "    [警告] tessdata 目录不可写($TESSDATA_DIR),请手动:"
      echo "      curl -L https://gh-proxy.com/https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/chi_sim.traineddata -o \"$TESSDATA_DIR/chi_sim.traineddata\""
      MISSING=1
    fi
  fi
fi

# yt-dlp(下载用;缺失不阻断,流水线运行时才需要)
if ! command -v yt-dlp >/dev/null 2>&1; then
  echo "    yt-dlp      未安装 → pip install yt-dlp (或 brew install yt-dlp)"
fi

if [ "$MISSING" -eq 1 ]; then
  echo ""
  echo "[中止] 请先安装上述缺失依赖再重跑本脚本。" >&2
  exit 1
fi

echo ""
echo "==> 2/3 编译 whisper-cli (模型 $MODEL_SIZE)"
if [ -x scripts/whisper-cli ]; then
  echo "    已存在 scripts/whisper-cli,跳过编译(删除后可强制重编)"
else
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  echo "    克隆 whisper.cpp 源码到 $TMP"
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git "$TMP/whisper.cpp"

  echo "    cmake 配置(macOS CLT 需显式指定 C++ 头文件路径)"
  cmake -B "$TMP/whisper.cpp/build" -S "$TMP/whisper.cpp" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_CXX_FLAGS="-isystem $(xcrun --show-sdk-path)/usr/include/c++/v1 -isystem $(xcrun --show-sdk-path)/usr/include"

  echo "    make -j $JOBS whisper-cli"
  make -C "$TMP/whisper.cpp/build" -j "$JOBS" whisper-cli

  cp "$TMP/whisper.cpp/build/bin/whisper-cli" scripts/whisper-cli
  chmod +x scripts/whisper-cli
  echo "    已安装 scripts/whisper-cli"
fi

echo ""
echo "==> 3/3 下载 whisper 模型 ggml-$MODEL_SIZE.bin"
MODEL_PATH="assets/ggml-$MODEL_SIZE.bin"
if [ -f "$MODEL_PATH" ]; then
  echo "    已存在 $MODEL_PATH,跳过下载"
else
  echo "    源: huggingface.co/ggerganov/whisper.cpp (约数百 MB,请耐心)"
  curl -fL --progress-bar \
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODEL_SIZE.bin" \
    -o "$MODEL_PATH"
fi

echo ""
echo "==> 验证"
python3 scripts/process_episode.py 2>&1 | head -3 || true
echo ""
echo "部署完成。下一步:"
echo "  python3 scripts/process_episode.py <工作目录> <P编号> <视频URL>"
