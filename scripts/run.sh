#!/bin/bash
# ============================================================
# 智护银伴 · 后端服务一键启动脚本
# ============================================================

# 设置项目根目录
PROJECT_DIR=$(dirname "$0")/..
cd "$PROJECT_DIR"

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
echo "服务将运行在 http://0.0.0.0:8000"
echo "按 CTRL+C 停止服务。"

uvicorn main:app --host 0.0.0.0 --port 8000
