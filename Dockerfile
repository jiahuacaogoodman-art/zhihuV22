# ============================================================
# 智护银伴 · Dockerfile
# 多阶段构建：builder 安装依赖 → runtime 精简镜像
#
# 推荐用法 ─ 直接用项目根目录的 docker-compose.yml 一键拉起：
#   docker compose up -d
#
# 单独构建/运行（手动模式）：
#   docker build -t zhihu-yinban:latest .
#   docker run -d \
#     -p 8000:8000 \
#     -e AUTH_TOKEN=$(openssl rand -hex 32) \
#     -e PII_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
#     -e OLLAMA_API_URL=http://host.docker.internal:11434/api/generate \
#     -v yinban_ehr_db:/app/local_ehr_db \
#     -v yinban_ehr_uploads:/app/local_ehr_uploads \
#     -v yinban_auth:/app/local_auth \
#     -v yinban_audit_log:/app/local_audit_log \
#     -v yinban_nursing_events:/app/local_nursing_events \
#     --add-host=host.docker.internal:host-gateway \
#     --name yinban \
#     zhihu-yinban:latest
#
# 注意：
#   - Ollama 通常跑在容器外。容器内访问宿主 Ollama 需要 host.docker.internal
#   - 生产环境请绑定内网地址，不要直接暴露 8000 端口到公网
#
# 跨架构支持：
#   本 Dockerfile 同时支持 linux/amd64 和 linux/arm64（M1/M2 Mac）。
#   通过 dpkg-architecture 自动探测多架构库路径，不再写死 x86_64。
# ============================================================

# ── Stage 1: builder ────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

# 构建参数：允许指定 APT 镜像源以加速中国大陆/校园网环境
ARG APT_MIRROR=""

# 切换 APT 镜像源（如果传入 APT_MIRROR）
RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
        sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi

# 系统依赖（Tesseract OCR + 中文语言包）
# 添加 Acquire::Retries 防止网络抖动
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        libgomp1 \
        dpkg-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制 requirements，利用 Docker 层缓存——代码改动不会触发重新安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ────────────────────────────────────────
FROM python:3.11-slim-bookworm AS runtime

WORKDIR /app

# 构建参数：允许指定 APT 镜像源
ARG APT_MIRROR=""

# 切换 APT 镜像源（如果传入 APT_MIRROR）
RUN if [ -n "$APT_MIRROR" ]; then \
        sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
        sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi

# 从 builder 复制已安装的 site-packages + 可执行文件
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin

# Tesseract 二进制 + 语言包
COPY --from=builder /usr/bin/tesseract /usr/bin/tesseract
COPY --from=builder /usr/share/tesseract-ocr /usr/share/tesseract-ocr

# ── 跨架构动态库复制 ──────────────────────────────────────────
# 使用 dpkg-architecture 探测实际架构多架构路径（x86_64-linux-gnu 或 aarch64-linux-gnu）
# 这样无论在 Intel 还是 ARM 机器上都能正确构建。
COPY --from=builder /usr/bin/dpkg-architecture /usr/bin/dpkg-architecture
RUN MULTIARCH=$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || echo "x86_64-linux-gnu") && \
    echo "Detected multiarch: ${MULTIARCH}"
# 直接安装运行时依赖而非手动复制 .so，更稳定且跨架构兼容
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
        libtesseract5 \
        libleptonica-dev \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户运行
RUN groupadd -r yinban && useradd -r -g yinban -d /app -s /bin/false yinban

# 复制应用代码
COPY --chown=yinban:yinban . .

# 数据目录（挂载卷）+ HuggingFace/模型缓存目录
# 注意：容器以非 root yinban 用户运行，所有挂载点和缓存目录必须
#       在切换用户前创建并 chown，否则会出 Permission Denied。
RUN mkdir -p \
        local_ehr_db \
        local_ehr_uploads \
        local_nursing_events \
        local_auth \
        local_audit_log \
        /app/.cache/huggingface \
        /app/.cache/torch \
        /app/.cache/chroma \
        /tmp/sentence_transformers \
    && chown -R yinban:yinban \
        local_ehr_db \
        local_ehr_uploads \
        local_nursing_events \
        local_auth \
        local_audit_log \
        /app/.cache \
        /tmp/sentence_transformers

USER yinban

# ── 环境变量默认值 ──────────────────────────────────────────
ENV HOST=0.0.0.0 \
    PORT=8000 \
    RELOAD=0 \
    WORKERS=1 \
    AUTH_TOKEN="" \
    PII_ENCRYPTION_KEY="" \
    OLLAMA_API_URL=http://host.docker.internal:11434/api/generate \
    OLLAMA_MODEL_NAME=hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # ── HuggingFace / Transformers 缓存目录 ──
    # 确保模型下载写入 yinban 用户可写的目录
    HF_HOME=/app/.cache/huggingface \
    HF_HUB_CACHE=/app/.cache/huggingface/hub \
    TRANSFORMERS_CACHE=/app/.cache/huggingface/transformers \
    SENTENCE_TRANSFORMERS_HOME=/tmp/sentence_transformers \
    XDG_CACHE_HOME=/app/.cache \
    # ── ChromaDB telemetry ──
    ANONYMIZED_TELEMETRY=False

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=5).status==200 else 1)"

# 用 sh -c 让 $WORKERS 可以被 docker run -e WORKERS=4 覆盖
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-1}"]
