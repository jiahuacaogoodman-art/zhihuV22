#!/bin/bash
# ============================================================
# 智护银伴 · 后端服务一键启动脚本
# ============================================================

# 设置项目根目录
PROJECT_DIR=$(dirname "$0")/..
cd "$PROJECT_DIR"

# ── 加载 .env 到当前 shell ────────────────────────────────
# 之前只检查 .env 是否存在，没真正加载，导致裸机启动时 AUTH_TOKEN /
# PII_ENCRYPTION_KEY / OLLAMA_MODEL_NAME 等可能全部读不到。
# main.py 里也有 load_dotenv() 兜底，但这里再 export 一遍：
#   1) 让本脚本里后续可能加的 echo $AUTH_TOKEN 也能 work
#   2) 让 uvicorn fork 出来的子进程一定看得见这些变量
# 设计：环境里已经显式 export 的值优先（systemd/docker 注入永远赢）。
if [ -f ".env" ]; then
    echo "正在加载 .env 文件..."
    set -a   # 之后所有赋值自动 export
    # shellcheck disable=SC1091
    . ./.env
    set +a
else
    echo "警告：未找到 .env 文件，将依赖系统环境变量。"
    echo "  建议先运行：./scripts/setup.sh  或  cp .env.example .env"
fi

# 检查虚拟环境是否存在，如果存在则激活
if [ -d "venv" ]; then
    echo "正在激活 Python 虚拟环境..."
    source venv/bin/activate
else
    echo "警告：未找到虚拟环境 (venv)，将使用系统 Python 环境。"
fi

# 检查 Ollama 服务是否在运行
if ! pgrep -x "ollama" > /dev/null
then
    echo "错误：Ollama 服务未运行。请先启动 Ollama。"
    exit 1
fi

# 启动 FastAPI 应用
echo "正在启动 智护银伴 后端服务..."
echo "服务将运行在 http://${HOST:-0.0.0.0}:${PORT:-8000}"
echo "按 CTRL+C 停止服务。"

uvicorn main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
