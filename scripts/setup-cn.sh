#!/usr/bin/env bash
# ============================================================
# 智护银伴 · 国内网络一键部署
# 无需 VPN、无需代理，自动使用国内镜像源
#
# 用法:
#   chmod +x scripts/setup-cn.sh
#   ./scripts/setup-cn.sh
# ============================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "\033[0;34mℹ\033[0m  $*"; }
success() { echo -e "${GREEN}✔${NC}  $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✘${NC}  $*"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

cd "$PROJECT_DIR"

echo ""
echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║     智护银伴 · 国内网络一键部署（无需梯子）     ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"
echo ""

# ── 检测 Docker ──────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    error "未检测到 Docker，请先安装："
    echo "  macOS:  brew install --cask docker"
    echo "  Linux:  curl -fsSL https://get.docker.com | sudo sh"
    exit 1
fi
if ! docker info &>/dev/null 2>&1; then
    error "Docker 未运行，请先启动 Docker Desktop 或 systemctl start docker"
    exit 1
fi
success "Docker 就绪"

# ── 检测 compose ─────────────────────────────────────────────
if docker compose version &>/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
else
    error "未检测到 Docker Compose"
    exit 1
fi
success "Compose 就绪"

# ── 生成 .env（如果不存在）────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    info "生成 .env 配置..."
    AUTH_TOKEN=$(openssl rand -hex 32)
    PII_KEY=$(openssl rand -base64 32 | tr -d '\n' | head -c 44)

    cat > "$ENV_FILE" << EOF
AUTH_TOKEN=${AUTH_TOKEN}
PII_ENCRYPTION_KEY=${PII_KEY}
HOST=0.0.0.0
PORT=8000
WORKERS=1
MAX_UPLOAD_SIZE_MB=15
EMBEDDING_ALLOW_DEGRADED=true
ANONYMIZED_TELEMETRY=False
HF_ENDPOINT=https://hf-mirror.com

# --- LLM Provider（默认 ollama，也可改成 openai 用远程 API）---
# LLM_PROVIDER=ollama
# LLM_PROVIDER=openai
# OPENAI_API_BASE=https://api.deepseek.com/v1
# OPENAI_MODEL=deepseek-chat
# OPENAI_API_KEY=sk-xxx

# --- Ollama 配置（LLM_PROVIDER=ollama 时生效）---
OLLAMA_MODEL_NAME=hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M
EOF
    success "已生成 .env（含 HF 国内镜像配置）"
    echo ""
    echo -e "  ${YELLOW}提示：${NC}默认使用本地 Ollama。如果你要用远程 API（DeepSeek/智谱/vLLM），"
    echo -e "  编辑 ${CYAN}.env${NC} 把 LLM_PROVIDER 改成 openai 并填写 OPENAI_API_BASE 等。"
    echo ""
else
    # 确保已有 .env 里加上 HF 镜像
    if ! grep -q "HF_ENDPOINT" "$ENV_FILE" 2>/dev/null; then
        echo "HF_ENDPOINT=https://hf-mirror.com" >> "$ENV_FILE"
        info "已追加 HF_ENDPOINT=https://hf-mirror.com 到 .env"
    fi
    success "使用已有 .env"
fi

# ── 配置 Docker Hub 镜像加速（提示）──────────────────────────
echo ""
info "提示：如果 docker pull 很慢，建议配置 Docker 镜像加速："
echo -e "  编辑 ${CYAN}/etc/docker/daemon.json${NC}（Linux）或 Docker Desktop 设置："
echo '  {"registry-mirrors": ["https://docker.mirrors.ustc.edu.cn"]}'
echo ""

# ── 构建（使用清华 APT 镜像）─────────────────────────────────
info "开始构建镜像（使用清华 APT 镜像加速）..."
echo ""

$COMPOSE build \
    --build-arg APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn \
    2>&1 | tail -20

echo ""
success "镜像构建完成"

# ── 启动 ─────────────────────────────────────────────────────
info "启动服务..."

# 根据 .env 里的 LLM_PROVIDER 决定是否启动 Ollama
LLM_PROVIDER=$(grep "^LLM_PROVIDER=" "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d ' ')
PROFILE_FLAG=""
if [[ "$LLM_PROVIDER" == "openai" ]]; then
    info "检测到 LLM_PROVIDER=openai，跳过 Ollama 容器"
else
    PROFILE_FLAG="--profile ollama"
    info "使用本地 Ollama（首次需下载模型）"
fi

$COMPOSE $PROFILE_FLAG up -d 2>&1 | sed 's/^/  /'
echo ""
success "容器已启动"

# ── 等模型下载（仅 Ollama 模式）────────────────────────────────
if [[ "$LLM_PROVIDER" != "openai" ]]; then
    info "等待 Ollama 模型下载（首次约 5-20 分钟）..."
    echo "  另开终端可看进度: $COMPOSE logs -f model-puller"

    MAX_WAIT=1800
    WAITED=0
    while [ $WAITED -lt $MAX_WAIT ]; do
        STATUS=$(docker inspect --format='{{.State.Status}}' yinban-model-puller 2>/dev/null || echo "unknown")
        if [[ "$STATUS" == "exited" ]]; then
            EXIT_CODE=$(docker inspect --format='{{.State.ExitCode}}' yinban-model-puller 2>/dev/null || echo "1")
            if [[ "$EXIT_CODE" == "0" ]]; then
                success "模型下载完成"
                break
            else
                warn "模型下载失败，查看: $COMPOSE logs model-puller"
                break
            fi
        fi
        sleep 10
        WAITED=$((WAITED + 10))
        echo -ne "\r  ⏳ 已等待 ${WAITED}s..."
    done
    echo ""
fi

# ── 等后端就绪 ───────────────────────────────────────────────
info "等待后端启动..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/health &>/dev/null; then
        success "后端就绪！"
        break
    fi
    sleep 4
done

# ── 完成 ─────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║            🎉  部署成功！                       ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"
echo ""
echo -e "  管理端：${CYAN}http://localhost:8000/${NC}"
echo -e "  护工端：${CYAN}http://localhost:8000/nurse${NC}"
echo ""

AUTH_TOKEN=$(grep "^AUTH_TOKEN=" "$ENV_FILE" | cut -d= -f2)
echo -e "  管理员 Token："
echo -e "  ${YELLOW}${AUTH_TOKEN}${NC}"
echo ""
echo -e "  查看完整配置: ${CYAN}cat .env${NC}"
echo -e "  查看日志:     ${CYAN}$COMPOSE logs -f app${NC}"
echo -e "  停止服务:     ${CYAN}$COMPOSE down${NC}"
echo ""
