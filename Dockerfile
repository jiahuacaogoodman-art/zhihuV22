# ============================================================
# 智护银伴 · Dockerfile
# 多阶段构建：builder 安装依赖 → runtime 精简镜像
#
# 构建：
#   docker build -t zhihu-yinban:latest .
#
# 运行（最小化示例，token 务必换成随机字符串）：
#   docker run -d \
#     -p 8000:8000 \
#     -e AUTH_TOKEN=$(openssl rand -hex 32) \
#     -v /data/yinban/ehr_db:/app/local_ehr_db \
#     -v /data/yinban/uploads:/app/local_ehr_uploads \
#     -v /data/yinban/events:/app/local_nursing_events \
#     --name yinban \
#     zhihu-yinban:latest
#
# 注意：
#   - Ollama / bge 模型在容器外运行，或挂载 ~/.ollama 和 HF 缓存目录
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
RUN mkdir -p local_ehr_db local_ehr_uploads local_nursing_events \
    && chown -R yinban:yinban local_ehr_db local_ehr_uploads local_nursing_events

USER yinban

# ── 环境变量默认值 ──────────────────────────────────────────
ENV HOST=0.0.0.0 \
    PORT=8000 \
    RELOAD=0 \
    AUTH_TOKEN="" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# uvicorn 直接启动（不走 python main.py，避免双重进程）
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
