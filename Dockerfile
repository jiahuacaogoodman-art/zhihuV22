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
# ============================================================

# ── Stage 1: builder ────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# 系统依赖（Tesseract OCR + 中文语言包；如不需要 OCR 可删去）
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 先复制 requirements，利用 Docker 层缓存——代码改动不会触发重新安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# 从 builder 复制已安装的 site-packages + 可执行文件
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin
# Tesseract 二进制 + 语言包
COPY --from=builder /usr/bin/tesseract /usr/bin/tesseract
COPY --from=builder /usr/share/tesseract-ocr /usr/share/tesseract-ocr
COPY --from=builder /usr/lib/x86_64-linux-gnu/libtesseract* \
                    /usr/lib/x86_64-linux-gnu/
COPY --from=builder /usr/lib/x86_64-linux-gnu/libleptonica* \
                    /usr/lib/x86_64-linux-gnu/

# 运行时依赖（libgomp for sentence-transformers）
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 非 root 用户运行
RUN groupadd -r yinban && useradd -r -g yinban -d /app yinban

# 复制应用代码
COPY --chown=yinban:yinban . .

# 数据目录（挂载卷）
# 注意：容器以非 root yinban 用户运行，所有挂载点必须在切换用户前创建并 chown，
#      否则 docker volume 首次挂载会以 root:root 出现，yinban 写不进去导致启动失败。
RUN mkdir -p local_ehr_db local_ehr_uploads local_nursing_events local_auth local_audit_log \
    && chown -R yinban:yinban \
        local_ehr_db local_ehr_uploads local_nursing_events local_auth local_audit_log

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
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=5).status==200 else 1)"

# 用 sh -c 让 $WORKERS 可以被 docker run -e WORKERS=4 覆盖
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-1}"]
