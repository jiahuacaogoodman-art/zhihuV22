#!/usr/bin/env bash
# ============================================================
# 智护银伴 · 一键部署向导
# ZhiHu YinBan · One-Click Deployment Wizard
#
# 用法 / Usage:
#   chmod +x scripts/setup.sh
#   ./scripts/setup.sh
#
# 它会：
#   1. 检测 Docker / Docker Compose
#   2. 自动生成 AUTH_TOKEN + PII_ENCRYPTION_KEY（或让你贴现有的）
#   3. 可选填远程 GPU API（OpenAI 兼容）
#   4. 检测本地 Ollama 或使用容器化 Ollama
#   5. 让你选模型量化档位（Q3/Q4/Q5/Q8/自定义）
#   6. 写入 .env
#   7. docker compose up -d
#   8. 等待健康检查通过
#   9. 输出访问地址 + 管理员 Token
# ============================================================

set -euo pipefail

# ── 颜色 ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ── 工具函数 ──────────────────────────────────────────────
info()    { echo -e "${BLUE}ℹ${NC}  $*"; }
success() { echo -e "${GREEN}✔${NC}  $*"; }
warn()    { echo -e "${YELLOW}⚠${NC}  $*"; }
error()   { echo -e "${RED}✘${NC}  $*"; }
header()  { echo -e "\n${BOLD}${CYAN}━━━ $* ━━━${NC}\n"; }
ask()     { echo -en "${BOLD}$*${NC}"; }

# 密钥脱敏显示：只展示前4位 + "****" + 末4位
mask_secret() {
    local secret="$1"
    local len=${#secret}
    if [ "$len" -le 8 ]; then
        echo "****"
    else
        echo "${secret:0:4}****${secret: -4}"
    fi
}

# 项目根目录（脚本所在位置的上一级）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

cd "$PROJECT_DIR"

# ══════════════════════════════════════════════════════════════
# Banner
# ══════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}${CYAN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║          智护银伴 · 一键部署向导                ║"
echo "  ║       ZhiHu YinBan Deployment Wizard            ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"
echo ""

# ══════════════════════════════════════════════════════════════
# Step 0: 代理 / 网络环境检测
# ══════════════════════════════════════════════════════════════
# 自动检测代理环境变量，帮助用户诊断网络问题
if [ -n "${HTTP_PROXY:-}" ] || [ -n "${HTTPS_PROXY:-}" ] || [ -n "${http_proxy:-}" ] || [ -n "${https_proxy:-}" ]; then
    info "检测到代理环境变量:"
    [ -n "${HTTP_PROXY:-}" ]  && echo -e "    HTTP_PROXY  = ${CYAN}${HTTP_PROXY}${NC}"
    [ -n "${HTTPS_PROXY:-}" ] && echo -e "    HTTPS_PROXY = ${CYAN}${HTTPS_PROXY}${NC}"
    [ -n "${http_proxy:-}" ]  && echo -e "    http_proxy  = ${CYAN}${http_proxy}${NC}"
    [ -n "${https_proxy:-}" ] && echo -e "    https_proxy = ${CYAN}${https_proxy}${NC}"
    echo ""
    info "Docker 构建时会自动继承宿主机代理（BuildKit / Docker Desktop）"
fi

# ══════════════════════════════════════════════════════════════
# Step 1: 检测 Docker
# ══════════════════════════════════════════════════════════════
header "Step 1/6 · 检测 Docker 环境"

if ! command -v docker &>/dev/null; then
    error "未检测到 Docker。"
    echo ""
    # 检测平台给出具体安装命令
    case "$(uname -s)" in
        Darwin)
            echo -e "  ${BOLD}macOS 安装方式（任选一种）：${NC}"
            echo ""
            echo "    方式 1（推荐，需先装 Homebrew）："
            echo -e "      ${CYAN}brew install --cask docker${NC}"
            echo "      安装后在启动台打开 Docker 图标，等顶栏出现鲸鱼即可"
            echo ""
            echo "    方式 2（手动下载）："
            if [[ "$(uname -m)" == "arm64" ]]; then
                echo -e "      ${CYAN}https://desktop.docker.com/mac/main/arm64/Docker.dmg${NC}"
            else
                echo -e "      ${CYAN}https://desktop.docker.com/mac/main/amd64/Docker.dmg${NC}"
            fi
            echo "      下载 → 拖进 Applications → 双击打开 → 等启动完成"
            ;;
        Linux)
            echo -e "  ${BOLD}Linux 一键安装：${NC}"
            echo ""
            echo -e "    ${CYAN}curl -fsSL https://get.docker.com | sudo sh${NC}"
            echo -e "    ${CYAN}sudo usermod -aG docker \$USER${NC}"
            echo ""
            echo "    安装后重新登录（或 newgrp docker），再重跑本脚本。"
            ;;
        *)
            echo "  请访问 https://docs.docker.com/engine/install/ 按平台安装"
            ;;
    esac
    echo ""
    ask "  是否现在自动尝试安装 Docker? [y/N]: "
    read -r auto_install_docker
    if [[ "$auto_install_docker" =~ ^[Yy] ]]; then
        case "$(uname -s)" in
            Darwin)
                if command -v brew &>/dev/null; then
                    info "正在通过 Homebrew 安装 Docker Desktop..."
                    brew install --cask docker
                    echo ""
                    warn "请在启动台打开 Docker 应用，等顶栏出现鲸鱼图标后重跑本脚本。"
                    exit 0
                else
                    error "未检测到 Homebrew，请手动安装 Docker Desktop："
                    echo "  https://docs.docker.com/desktop/install/mac-install/"
                    exit 1
                fi
                ;;
            Linux)
                info "正在安装 Docker..."
                curl -fsSL https://get.docker.com | sudo sh
                sudo usermod -aG docker "$USER"
                echo ""
                warn "Docker 已安装。请重新登录终端（或运行 newgrp docker），然后重跑本脚本。"
                exit 0
                ;;
            *)
                error "不支持自动安装，请手动安装后重跑本脚本。"
                exit 1
                ;;
        esac
    else
        info "安装完 Docker 后重跑本脚本即可。"
        exit 0
    fi
fi
success "Docker 已安装: $(docker --version | head -1)"

# 检测 compose（V2 插件 或 独立 docker-compose）
COMPOSE_CMD=""
if docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
    success "Docker Compose V2: $(docker compose version --short 2>/dev/null || echo 'ok')"
elif command -v docker-compose &>/dev/null; then
    COMPOSE_CMD="docker-compose"
    success "docker-compose (V1): $(docker-compose --version | head -1)"
else
    error "未检测到 Docker Compose。请安装："
    echo "  https://docs.docker.com/compose/install/"
    exit 1
fi

# 检测 Docker daemon 是否在跑
if ! docker info &>/dev/null 2>&1; then
    error "Docker daemon 未运行。请先启动 Docker 服务。"
    echo "  sudo systemctl start docker  (Linux)"
    echo "  或打开 Docker Desktop        (macOS/Windows)"
    exit 1
fi
success "Docker daemon 正在运行"

# ══════════════════════════════════════════════════════════════
# Step 2: 生成/设置密钥
# ══════════════════════════════════════════════════════════════
header "Step 2/6 · 配置安全密钥"

# --- AUTH_TOKEN ---
echo -e "  ${BOLD}管理员 Token (AUTH_TOKEN)${NC}"
echo "  这是部署后的管理员登录凭证，相当于超级密码。"
echo ""

if [ -f "$ENV_FILE" ] && grep -q "^AUTH_TOKEN=.\+" "$ENV_FILE" 2>/dev/null; then
    EXISTING_TOKEN=$(grep "^AUTH_TOKEN=" "$ENV_FILE" | cut -d= -f2)
    echo -e "  已有 Token: ${YELLOW}${EXISTING_TOKEN:0:8}...${NC}"
    ask "  是否保留现有 Token? [Y/n]: "
    read -r keep_token
    if [[ "$keep_token" =~ ^[Nn] ]]; then
        AUTH_TOKEN=$(openssl rand -hex 32)
        success "已生成新 AUTH_TOKEN"
        echo -e "    ${YELLOW}${AUTH_TOKEN}${NC}"
    else
        AUTH_TOKEN="$EXISTING_TOKEN"
        success "保留现有 AUTH_TOKEN"
    fi
else
    ask "  自动生成随机 Token? [Y/n]: "
    read -r auto_token
    if [[ "$auto_token" =~ ^[Nn] ]]; then
        ask "  请粘贴你的 AUTH_TOKEN: "
        read -r AUTH_TOKEN
        if [ -z "$AUTH_TOKEN" ]; then
            error "Token 不能为空"
            exit 1
        fi
    else
        AUTH_TOKEN=$(openssl rand -hex 32)
        success "已自动生成 AUTH_TOKEN"
        echo -e "    ${YELLOW}${AUTH_TOKEN}${NC}"
        echo ""
        echo -e "    ${RED}⚠ 请立即复制保存，部署完成后也会再次显示。${NC}"
    fi
fi
echo ""

# --- PII_ENCRYPTION_KEY ---
echo -e "  ${BOLD}PII 加密密钥 (PII_ENCRYPTION_KEY)${NC}"
echo "  用于加密病历中的敏感信息（姓名、身份证、联系方式等）。"
echo ""

if [ -f "$ENV_FILE" ] && grep -q "^PII_ENCRYPTION_KEY=.\+" "$ENV_FILE" 2>/dev/null; then
    EXISTING_PII=$(grep "^PII_ENCRYPTION_KEY=" "$ENV_FILE" | cut -d= -f2)
    echo -e "  已有密钥: ${YELLOW}${EXISTING_PII:0:8}...${NC}"
    ask "  是否保留现有密钥? [Y/n]: "
    read -r keep_pii
    if [[ "$keep_pii" =~ ^[Nn] ]]; then
        PII_ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || openssl rand -base64 32)
        success "已生成新 PII_ENCRYPTION_KEY"
        echo -e "    ${YELLOW}${PII_ENCRYPTION_KEY}${NC}"
    else
        PII_ENCRYPTION_KEY="$EXISTING_PII"
        success "保留现有 PII_ENCRYPTION_KEY"
    fi
else
    # 尝试用 python3 生成 Fernet key，失败则用 openssl 兜底
    if python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" &>/dev/null; then
        PII_ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    else
        # Docker 容器里有 cryptography，这里用 openssl 生成一个合法 base64
        PII_ENCRYPTION_KEY=$(openssl rand -base64 32 | tr -d '\n' | head -c 44)
        warn "本机无 cryptography 库，使用 openssl 生成密钥（Docker 内会正常工作）"
    fi
    success "已自动生成 PII_ENCRYPTION_KEY"
    echo -e "    ${YELLOW}${PII_ENCRYPTION_KEY}${NC}"
fi
echo ""

# ══════════════════════════════════════════════════════════════
# Step 3: 远程 GPU / OpenAI 兼容 API（可选）
# ══════════════════════════════════════════════════════════════
header "Step 3/6 · LLM 推理后端选择"

echo "  项目支持两种 LLM 推理方式："
echo ""
echo -e "    ${BOLD}[1] 本地 Ollama（默认，推荐）${NC}"
echo "        Docker Compose 自动启动 Ollama + 自动下载模型"
echo "        适合：单机部署 / 没有 GPU 服务器 / 离线优先"
echo ""
echo -e "    ${BOLD}[2] 远程 GPU / OpenAI 兼容 API${NC}"
echo "        指向机房 vLLM / TGI / DeepSeek / 智谱等"
echo "        适合：有专门的 GPU 卡跑推理"
echo ""
ask "  选择 [1/2] (默认 1): "
read -r llm_choice
echo ""

LLM_PROVIDER="ollama"
OPENAI_API_BASE=""
OPENAI_MODEL=""
OPENAI_API_KEY=""

if [[ "$llm_choice" == "2" ]]; then
    LLM_PROVIDER="openai"
    echo -e "  ${BOLD}OpenAI 兼容端点配置${NC}"
    echo "  示例："
    echo "    vLLM:      http://192.168.1.100:8000/v1"
    echo "    TGI:       http://gpu-host:8080/v1"
    echo "    DeepSeek:  https://api.deepseek.com/v1"
    echo ""
    ask "  OPENAI_API_BASE (必填): "
    read -r OPENAI_API_BASE
    if [ -z "$OPENAI_API_BASE" ]; then
        error "OPENAI_API_BASE 不能为空"
        exit 1
    fi
    ask "  OPENAI_MODEL (必填，如 Qwen/Qwen2.5-7B-Instruct): "
    read -r OPENAI_MODEL
    if [ -z "$OPENAI_MODEL" ]; then
        error "OPENAI_MODEL 不能为空"
        exit 1
    fi
    ask "  OPENAI_API_KEY (选填，自建服务留空): "
    read -r OPENAI_API_KEY
    echo ""
    success "已配置远程 LLM: $OPENAI_API_BASE → $OPENAI_MODEL"
fi

# ══════════════════════════════════════════════════════════════
# Step 4: 检测 Ollama + 模型选择
# ══════════════════════════════════════════════════════════════
OLLAMA_MODEL_NAME="hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M"

if [[ "$LLM_PROVIDER" == "ollama" ]]; then
    header "Step 4/6 · Ollama 模型配置"

    # 检测宿主机是否已有 Ollama 在跑
    LOCAL_OLLAMA=false
    if curl -s http://localhost:11434/ &>/dev/null 2>&1; then
        LOCAL_OLLAMA=true
        success "检测到本地 Ollama 服务已运行 (localhost:11434)"
        echo ""
        echo "  已有模型列表："
        ollama list 2>/dev/null | head -10 || echo "  (无法获取)"
        echo ""
        warn "Docker Compose 仍会启动容器化 Ollama（端口不冲突，绑 127.0.0.1:11434 → 容器内）"
        echo "  如果你想用宿主机的 Ollama，部署后可手动修改 OLLAMA_API_URL。"
    else
        info "未检测到本地 Ollama（将使用 Docker Compose 自带的容器化 Ollama）"
    fi
    echo ""

    # 模型选择
    echo -e "  ${BOLD}选择模型量化档位：${NC}"
    echo ""
    echo "    [1] Q3_K_M  ~3.9 GB  (极省内存，8GB 内存可跑，质量略降)"
    echo "    [2] Q4_K_M  ~4.8 GB  (默认推荐，16GB 内存流畅) ⭐"
    echo "    [3] Q5_K_M  ~5.5 GB  (推荐质量，需 12GB+ 内存)"
    echo "    [4] Q8_0    ~8.2 GB  (接近无损，需 16GB+ 内存)"
    echo "    [5] 自定义模型名（如 qwen2.5:7b 或其它 HF GGUF）"
    echo ""
    ask "  选择 [1-5] (默认 2): "
    read -r model_choice
    echo ""

    case "${model_choice:-2}" in
        1) OLLAMA_MODEL_NAME="hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q3_K_M"
           success "已选择 Q3_K_M (约 3.9 GB)" ;;
        2) OLLAMA_MODEL_NAME="hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M"
           success "已选择 Q4_K_M (约 4.8 GB) — 推荐" ;;
        3) OLLAMA_MODEL_NAME="hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q5_K_M"
           success "已选择 Q5_K_M (约 5.5 GB)" ;;
        4) OLLAMA_MODEL_NAME="hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q8_0"
           success "已选择 Q8_0 (约 8.2 GB)" ;;
        5) ask "  请输入完整模型名: "
           read -r OLLAMA_MODEL_NAME
           if [ -z "$OLLAMA_MODEL_NAME" ]; then
               OLLAMA_MODEL_NAME="hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M"
               warn "输入为空，使用默认 Q4_K_M"
           else
               success "已设置自定义模型: $OLLAMA_MODEL_NAME"
           fi ;;
        *) success "使用默认 Q4_K_M (约 4.8 GB)" ;;
    esac

    # GPU 检测
    echo ""
    HAS_GPU=false
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null 2>&1; then
        HAS_GPU=true
        success "检测到 NVIDIA GPU:"
        nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | sed 's/^/        /'
        echo ""
        ask "  启用 GPU 加速? [Y/n]: "
        read -r use_gpu
        if [[ "$use_gpu" =~ ^[Nn] ]]; then
            HAS_GPU=false
            info "不使用 GPU"
        else
            success "将启用 GPU 加速 (docker-compose.gpu.yml)"
        fi
    else
        info "未检测到 NVIDIA GPU（将使用 CPU 推理，速度稍慢但完全可用）"
    fi
else
    header "Step 4/6 · 跳过（使用远程 API）"
    info "LLM_PROVIDER=openai，无需配置本地模型"
    HAS_GPU=false
fi

# ══════════════════════════════════════════════════════════════
# Step 5: 写入 .env
# ══════════════════════════════════════════════════════════════
header "Step 5/6 · 生成配置文件"

cat > "$ENV_FILE" << EOF
# ============================================================
# 智护银伴 · 部署配置（由 setup.sh 自动生成）
# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')
# ============================================================

# --- 鉴权 ---
AUTH_TOKEN=${AUTH_TOKEN}

# --- PII 加密 ---
PII_ENCRYPTION_KEY=${PII_ENCRYPTION_KEY}

# --- 服务监听 ---
HOST=0.0.0.0
PORT=8000

# --- LLM Provider ---
LLM_PROVIDER=${LLM_PROVIDER}

# --- Ollama 配置 ---
OLLAMA_MODEL_NAME=${OLLAMA_MODEL_NAME}
# OLLAMA_API_URL=http://localhost:11434/api/generate

# --- OpenAI 兼容配置（LLM_PROVIDER=openai 时生效）---
OPENAI_API_BASE=${OPENAI_API_BASE}
OPENAI_MODEL=${OPENAI_MODEL}
OPENAI_API_KEY=${OPENAI_API_KEY}

# --- 其它 ---
MAX_UPLOAD_SIZE_MB=15
WORKERS=1
RELOAD=0
EOF

success "配置已写入: ${CYAN}${ENV_FILE}${NC}"
echo ""
echo "  生成的关键配置："
echo -e "    AUTH_TOKEN         = ${YELLOW}$(mask_secret "$AUTH_TOKEN")${NC}"
echo -e "    PII_ENCRYPTION_KEY = ${YELLOW}$(mask_secret "$PII_ENCRYPTION_KEY")${NC}"
echo -e "    LLM Provider       = ${CYAN}${LLM_PROVIDER}${NC}"
if [[ "$LLM_PROVIDER" == "ollama" ]]; then
    echo -e "    模型               = ${CYAN}${OLLAMA_MODEL_NAME}${NC}"
else
    echo -e "    API Base           = ${CYAN}${OPENAI_API_BASE}${NC}"
    echo -e "    模型               = ${CYAN}${OPENAI_MODEL}${NC}"
    if [ -n "$OPENAI_API_KEY" ]; then
        echo -e "    API Key            = ${YELLOW}$(mask_secret "$OPENAI_API_KEY")${NC}"
    fi
fi
echo ""
echo -e "  ${BLUE}ℹ${NC}  完整密钥保存在 ${CYAN}${ENV_FILE}${NC}，请妥善保管该文件"
echo -e "  ${RED}⚠ 请勿截图/录屏时暴露 .env 文件内容${NC}"

# ══════════════════════════════════════════════════════════════
# Step 6: 启动 Docker Compose
# ══════════════════════════════════════════════════════════════
header "Step 6/6 · 启动服务"

echo ""
ask "  确认启动部署? [Y/n]: "
read -r confirm
if [[ "$confirm" =~ ^[Nn] ]]; then
    info "已取消。配置文件已保存，你可以稍后手动运行："
    echo ""
    echo -e "    ${CYAN}$COMPOSE_CMD up -d${NC}"
    echo ""
    echo -e "  密钥已保存在 ${CYAN}${ENV_FILE}${NC}，查看完整配置："
    echo -e "    ${CYAN}cat ${ENV_FILE}${NC}"
    exit 0
fi
echo ""

# 构建 compose 命令
COMPOSE_FILES="-f docker-compose.yml"
COMPOSE_PROFILES=""
BUILD_ARGS=""

if [[ "$HAS_GPU" == "true" ]]; then
    COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.gpu.yml"
    info "启用 GPU overlay"
fi
if [[ "$LLM_PROVIDER" == "ollama" ]]; then
    COMPOSE_PROFILES="--profile ollama"
    info "启用本地 Ollama（首次需下载模型，约 5-20 分钟）"
else
    info "使用远程 API，跳过 Ollama 容器（节省 ~5 GB 磁盘 + 内存）"
fi

# 网络优化：中国大陆/校园网环境可使用镜像加速
echo ""
echo -e "  ${BOLD}构建选项（网络优化）：${NC}"
echo ""
echo -e "    ${BOLD}[1] 默认（直接构建，适合海外/科学上网）${NC}"
echo -e "    ${BOLD}[2] 国内加速（使用清华 APT 镜像 + 不强制 pull 基础镜像）${NC}"
echo -e "    ${BOLD}[3] 离线/缓存优先（不拉取任何远程资源，仅用本地缓存）${NC}"
echo ""
ask "  选择 [1-3] (默认 1): "
read -r network_choice
echo ""

DOCKER_BUILD_EXTRA=""
case "${network_choice:-1}" in
    2)
        DOCKER_BUILD_EXTRA="--build-arg APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn"
        info "使用清华 APT 镜像加速 Debian 包下载"
        ;;
    3)
        DOCKER_BUILD_EXTRA="--no-pull"
        info "离线模式：跳过基础镜像拉取，仅使用本地缓存"
        ;;
    *)
        info "使用默认网络设置"
        ;;
esac

COMPOSE_UP_CMD="$COMPOSE_CMD $COMPOSE_FILES $COMPOSE_PROFILES up -d --build $DOCKER_BUILD_EXTRA"
info "正在启动服务..."
echo ""
echo -e "  ${BOLD}$ ${COMPOSE_UP_CMD}${NC}"
echo ""

$COMPOSE_UP_CMD 2>&1 | sed 's/^/  /'

echo ""
success "容器已启动"

# ── 等待模型下载（如果是 Ollama 模式）────────────────────────
if [[ "$LLM_PROVIDER" == "ollama" ]]; then
    echo ""
    info "正在等待模型下载完成（首次约 5-20 分钟，取决于网速）..."
    echo "  你可以在另一个终端查看进度：$COMPOSE_CMD logs -f model-puller"
    echo ""

    # 最多等 30 分钟
    MAX_WAIT=1800
    WAITED=0
    INTERVAL=10

    while [ $WAITED -lt $MAX_WAIT ]; do
        # 检查 model-puller 是否已退出
        STATUS=$(docker inspect --format='{{.State.Status}}' yinban-model-puller 2>/dev/null || echo "unknown")
        if [[ "$STATUS" == "exited" ]]; then
            EXIT_CODE=$(docker inspect --format='{{.State.ExitCode}}' yinban-model-puller 2>/dev/null || echo "1")
            if [[ "$EXIT_CODE" == "0" ]]; then
                success "模型下载完成！"
                break
            else
                error "模型下载失败 (exit code: $EXIT_CODE)"
                echo "  查看日志：$COMPOSE_CMD logs model-puller"
                exit 1
            fi
        fi
        echo -ne "\r  ⏳ 已等待 ${WAITED}s / 模型下载中..."
        sleep $INTERVAL
        WAITED=$((WAITED + INTERVAL))
    done

    if [ $WAITED -ge $MAX_WAIT ]; then
        warn "等待超时（30分钟），模型可能仍在下载中"
        echo "  请手动检查：$COMPOSE_CMD logs -f model-puller"
    fi
fi

# ── 等待 app 健康检查 ────────────────────────────────────────
echo ""
info "等待后端启动..."

MAX_WAIT=120
WAITED=0
INTERVAL=5

while [ $WAITED -lt $MAX_WAIT ]; do
    if curl -sf http://localhost:8000/health &>/dev/null; then
        success "后端服务已就绪！"
        break
    fi
    echo -ne "\r  ⏳ 等待后端启动 ${WAITED}s..."
    sleep $INTERVAL
    WAITED=$((WAITED + INTERVAL))
done

if [ $WAITED -ge $MAX_WAIT ]; then
    warn "后端启动超时，请检查日志："
    echo "  $COMPOSE_CMD logs app"
    exit 1
fi

# ══════════════════════════════════════════════════════════════
# 完成！打印访问信息
# ══════════════════════════════════════════════════════════════
echo ""
echo ""
echo -e "${BOLD}${GREEN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║           🎉  部署成功！                        ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"
echo ""
echo -e "  ${BOLD}访问地址：${NC}"
echo -e "    管理端：  ${CYAN}http://localhost:8000/${NC}"
echo -e "    护工端：  ${CYAN}http://localhost:8000/nurse${NC}"
echo -e "    健康检查：${CYAN}http://localhost:8000/health${NC}"
echo ""
echo -e "  ${BOLD}管理员 Token（请妥善保管）：${NC}"
echo -e "    ${YELLOW}$(mask_secret "$AUTH_TOKEN")${NC}"
echo -e "    完整值请查看: ${CYAN}grep AUTH_TOKEN ${ENV_FILE}${NC}"
echo ""
echo -e "  ${BOLD}PII 加密密钥（备份用，丢失将无法解密已有数据）：${NC}"
echo -e "    ${YELLOW}$(mask_secret "$PII_ENCRYPTION_KEY")${NC}"
echo -e "    完整值请查看: ${CYAN}grep PII_ENCRYPTION_KEY ${ENV_FILE}${NC}"
echo ""
echo -e "  ${BOLD}使用方法：${NC}"
echo "    1. 打开浏览器访问 http://localhost:8000/"
echo "    2. 在左侧栏 Token 输入框粘贴上面的 Token"
echo "    3. 开始录入老人档案 → 上传病历 → 获取 AI 护理建议"
echo ""
echo -e "  ${BOLD}常用命令：${NC}"
echo "    查看状态：  $COMPOSE_CMD ps"
echo "    查看日志：  $COMPOSE_CMD logs -f app"
echo "    停止服务：  $COMPOSE_CMD down"
echo "    重启应用：  $COMPOSE_CMD restart app"
echo ""
if [[ "$LLM_PROVIDER" == "openai" ]]; then
    echo -e "  ${BOLD}LLM 后端：${NC} 远程 API → ${CYAN}${OPENAI_API_BASE}${NC}"
else
    echo -e "  ${BOLD}LLM 后端：${NC} 本地 Ollama → ${CYAN}${OLLAMA_MODEL_NAME}${NC}"
fi
echo ""
echo -e "  ${BOLD}数据安全：${NC}"
echo "    ✅ 所有数据保存在 Docker Volume 中，不出本机"
echo "    ✅ PII 字段已 Fernet 加密"
echo "    ✅ 操作审计已开启"
echo ""
echo -e "  ${BOLD}配置文件：${NC}"
echo -e "    ${CYAN}${ENV_FILE}${NC}"
echo "    包含所有密钥和配置，可用 cat .env 查看"
echo ""
echo "  ────────────────────────────────────────────────"
echo -e "  如果这个项目帮到了你，请给个 ⭐"
echo -e "  ${CYAN}https://github.com/jiahuacaogoodman-art/Zhihu-Yinban${NC}"
echo ""
echo -e "  ${BOLD}📮 商业合作：${NC}"
echo "    民营养老机构如有商业合作意向，可联系获取正式版："
echo -e "    ${CYAN}jiahuacaogoodman@gmail.com${NC}"
echo ""
