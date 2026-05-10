#!/usr/bin/env bash
# ============================================================================
# 把前端依赖的 CDN 资源下载到本地 static/vendor/，让系统完全离线可用。
# 只在首次离线部署时跑一次。之后 index.html/nurse.html 里的
#   https://cdn.jsdelivr.net/... → /static/vendor/...
# 需要你手动替换（或参考 README 最后）。
# ============================================================================
set -e
VENDOR_DIR="$(dirname "$0")/../static/vendor"
mkdir -p "$VENDOR_DIR"

download() {
  local url="$1"; local out="$2"
  echo "→ $out"
  curl -fsSL "$url" -o "$VENDOR_DIR/$out"
}

# Lucide Icons (ISC)
download "https://unpkg.com/lucide@0.454.0/dist/umd/lucide.min.js" "lucide.min.js"

# GSAP 3.12 + ScrollTrigger (Standard)
download "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js"          "gsap.min.js"
download "https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js" "ScrollTrigger.min.js"

# Lottie Player (MIT)
download "https://cdn.jsdelivr.net/npm/@lottiefiles/lottie-player@2.0.8/dist/lottie-player.js" "lottie-player.js"

echo
echo "完成。现在把 static/design/vendors.js 里的 URL 前缀改为 /static/vendor/ 即可离线运行。"
