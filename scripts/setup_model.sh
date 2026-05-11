#!/usr/bin/env bash
# ------------------------------------------------------------
# setup_model.sh —— 一键装好本项目需要的 huatuo_o1_7b 模型
#
# 做的事：
#   1. 检查 ollama 命令是否存在；没有就提示装
#   2. 检查 ollama 服务是否起来；没起来就提示怎么启动
#   3. 幂等拉取 cliu/HuatuoGPT-o1-7B（已存在则跳过）
#   4. 幂等 cp 成本项目用的别名 huatuo_o1_7b（已有则跳过）
#   5. 跑一个 5 秒的冒烟测试，确认模型真能回话
#
# 重复执行安全，中途断网再跑一次会接着上次来。
# ------------------------------------------------------------

set -euo pipefail

UPSTREAM="cliu/HuatuoGPT-o1-7B:latest"
ALIAS="huatuo_o1_7b"

# ---- 颜色（没有 tty 时自动退化为空串）----
if [ -t 1 ]; then
    G=$'\033[0;32m'; Y=$'\033[0;33m'; R=$'\033[0;31m'; B=$'\033[0;36m'; N=$'\033[0m'
else
    G=""; Y=""; R=""; B=""; N=""
fi

say()  { printf "%s[setup]%s %s\n" "$B" "$N" "$*"; }
ok()   { printf "%s[ ok ]%s %s\n" "$G" "$N" "$*"; }
warn() { printf "%s[warn]%s %s\n" "$Y" "$N" "$*"; }
die()  { printf "%s[fail]%s %s\n" "$R" "$N" "$*" >&2; exit 1; }

# ---- 1. ollama 命令 ----
if ! command -v ollama >/dev/null 2>&1; then
    warn "未检测到 ollama 命令"
    cat <<'EOF'

    请先安装 Ollama（运行时 + CLI）：

      Linux / macOS:
        curl -fsSL https://ollama.com/install.sh | sh

      Windows:
        https://ollama.com/download/windows

    安装完后重新执行本脚本即可。
EOF
    exit 1
fi
ok "ollama 已安装：$(ollama --version 2>/dev/null | head -1)"

# ---- 2. ollama 服务 ----
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    warn "ollama 服务未响应 http://localhost:11434"
    cat <<'EOF'

    请先启动 ollama 服务：

      Linux (systemd 安装方式):
        sudo systemctl start ollama

      macOS (桌面应用):
        打开 Ollama.app

      任意平台 (手动前台运行):
        ollama serve

    起来之后重新执行本脚本。
EOF
    exit 1
fi
ok "ollama 服务已就绪（:11434）"

# ---- 3. 拉上游模型 ----
if ollama list | awk '{print $1}' | grep -Fxq "$UPSTREAM"; then
    ok "已有 $UPSTREAM，跳过下载"
else
    say "开始下载 $UPSTREAM（约 8 GB，视网速需几分钟到半小时）"
    if ! ollama pull "$UPSTREAM"; then
        cat <<EOF

${R}下载失败。${N}常见原因：
  1. 网络连不上 Ollama Registry（大陆网络常见）—— 挂代理后重试；
  2. 磁盘空间不足 —— 至少预留 12 GB；
  3. 该名字已被上游删除 —— 改走"断网部署"方案，
     请看 README.md 里【断网部署：从 GGUF 导入】章节。
EOF
        exit 1
    fi
    ok "下载完成"
fi

# ---- 4. 建立本项目别名 ----
if ollama list | awk '{print $1}' | grep -Fxq "${ALIAS}:latest"; then
    ok "已有别名 $ALIAS，跳过"
else
    say "创建别名：$UPSTREAM → $ALIAS"
    ollama cp "$UPSTREAM" "$ALIAS"
    ok "别名创建完成"
fi

# ---- 5. 冒烟测试 ----
say "冒烟测试：让模型回一句话"
REPLY=$(curl -sf --max-time 120 http://localhost:11434/api/generate \
        -d "{\"model\":\"$ALIAS\",\"prompt\":\"用一句话介绍你自己\",\"stream\":false}" \
        | sed -n 's/.*"response":"\([^"]*\)".*/\1/p' | head -c 80)

if [ -z "$REPLY" ]; then
    warn "冒烟测试没收到回话 —— 可能是首次加载权重慢（>120s），稍后手动再试："
    echo "    ollama run $ALIAS \"你好\""
else
    ok "模型回话：${REPLY}..."
fi

cat <<EOF

${G}=== 全部搞定 ===${N}

下一步：

    cp .env.example .env          # 如果还没配环境变量
    uvicorn main:app --host 0.0.0.0 --port 8000

服务起来后访问 http://localhost:8000/

EOF
