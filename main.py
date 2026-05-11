# -*- coding: utf-8 -*-
"""
@Time    : 2026/03/08 10:10
@Author  : jiahuaCao
@File    : main.py
@Desc    : "智护银伴" 后端应用主入口
"""

import os
import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from contextlib import asynccontextmanager
import chromadb
from sentence_transformers import SentenceTransformer
from loguru import logger
from pathlib import Path

from app.core.config import (
    CHROMA_DB_PATH,
    CHROMA_COLLECTION_NAME,
    EMBEDDING_MODEL_NAME,
    EMBEDDING_DEVICE,
    EHR_UPLOAD_DIR,
    AUTH_TOKEN,
)
from app.middleware.auth import AuthTokenMiddleware
from app.routers import ehr, nursing

# ----------------------------------------------------------------
# 关键修复：使用 Path(__file__).resolve() 获取绝对路径
# 无论在 Windows 还是 Linux、无论从哪个目录启动，路径始终正确
# ----------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"
UPLOAD_DIR = Path(EHR_UPLOAD_DIR)

# 全局应用状态字典，存储 ChromaDB 连接和 Embedding 模型实例
app_state = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 应用的生命周期事件，在应用启动时执行初始化，在应用关闭时执行清理。
    """
    # --- 应用启动时 ---
    logger.info("应用启动中...")
    logger.info(f"项目根目录: {BASE_DIR}")
    logger.info(f"前端页面路径: {INDEX_HTML}")

    # 1. 初始化 ChromaDB 客户端
    logger.info(f"正在连接本地 ChromaDB，数据存储路径: {CHROMA_DB_PATH}")
    try:
        db_client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
        app_state["db_client"] = db_client
        logger.success("ChromaDB 连接成功！")

        # 2. 获取或创建 Collection
        collection = db_client.get_or_create_collection(
            name=CHROMA_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )
        app_state["db_collection"] = collection
        logger.success(f"ChromaDB Collection '{CHROMA_COLLECTION_NAME}' 加载成功！")

    except Exception as e:
        logger.error(f"ChromaDB 初始化失败: {e}")
        raise RuntimeError(f"ChromaDB 初始化失败: {e}") from e

    # 3. 加载 Embedding 模型
    logger.info(f"正在加载 Embedding 模型: {EMBEDDING_MODEL_NAME}，使用设备: {EMBEDDING_DEVICE}")
    try:
        embedding_function = SentenceTransformer(EMBEDDING_MODEL_NAME, device=EMBEDDING_DEVICE)
        app_state["embedding_function"] = embedding_function
        app_state["db_collection"].embedding_function = embedding_function
        logger.success("Embedding 模型加载成功！")
    except Exception as e:
        logger.error(f"Embedding 模型加载失败: {e}")
        logger.warning("请确保模型 'BAAI/bge-small-zh-v1.5' 已被下载并可在 sentence-transformers 中访问。")
        raise RuntimeError(f"Embedding 模型加载失败: {e}") from e

    logger.info("所有核心模块初始化完成，应用准备就绪！")
    yield

    # --- 应用关闭时 ---
    logger.info("应用关闭中...")
    app_state.clear()
    logger.info("资源已清理，应用已关闭。")


# 创建 FastAPI 应用实例
app = FastAPI(
    title="智护银伴 - 本地 RAG 核心后端",
    description="一个为基层养老院设计的、100%本地化运行的AI护理辅助系统。",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,    # 禁用 /docs Swagger 页面，避免与前端混淆
    redoc_url=None    # 禁用 /redoc 页面
)

# 挂载 API 路由
app.include_router(ehr.router, prefix="/api", tags=["EHR Management"])
app.include_router(nursing.router, prefix="/api", tags=["Nursing Decision Support"])

# ----------------------------------------------------------------
# 鉴权中间件：保护 /api/* 和 /uploads/*
# AUTH_TOKEN 留空则关闭（开发模式）；生产部署必须通过环境变量设置。
# ----------------------------------------------------------------
app.add_middleware(AuthTokenMiddleware, token=AUTH_TOKEN)
if AUTH_TOKEN:
    logger.info("鉴权已启用：/api/* 和 /uploads/* 需要 X-Auth-Token 请求头")
else:
    logger.warning("AUTH_TOKEN 未配置，鉴权已关闭（仅限开发环境，生产请设置环境变量 AUTH_TOKEN）")

# ----------------------------------------------------------------
# 关键修复：使用绝对路径挂载静态文件目录
# 确保 Windows 和 Linux 下均能正确找到 static/ 文件夹
# ----------------------------------------------------------------
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    logger.info(f"静态文件目录已挂载: {STATIC_DIR}")
else:
    logger.warning(f"未找到静态文件目录: {STATIC_DIR}，前端页面将不可用")

# 病历照片原件访问目录：文件仍保存在本地磁盘，仅在内网服务中按 URL 预览。
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
logger.info(f"病历照片上传目录已挂载: {UPLOAD_DIR}")

# ----------------------------------------------------------------
# 全局异常处理：按异常类型分层处理
# ----------------------------------------------------------------
# 设计要点：
# 1. HTTPException / StarletteHTTPException（404、400、413、503 等）走 FastAPI
#    的默认逻辑，保留业务路由里手写的 status_code 和 detail，不能被兜底成 500。
# 2. RequestValidationError（422）保留 Pydantic 的字段级错误信息，便于前端排错。
# 3. 只有真正未处理的 Exception 才落进 500 通道；500 响应体里**不能**回写
#    原始 exception 字符串，避免泄露内部路径 / SQL / 堆栈。完整信息只进日志。
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if exc.detail is not None else ""
    if request.url.path.startswith("/api") or exc.status_code >= 500:
        logger.warning(f"HTTPException {exc.status_code} at {request.url}: {detail}")
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": str(detail) or "request failed"},
        headers=getattr(exc, "headers", None) or None,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.info(f"请求校验失败 at {request.url}: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={
            "code": 422,
            "message": "请求参数校验失败",
            "errors": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # 用 logger.exception 保留完整堆栈到服务端日志；响应体里不回写 exc。
    logger.exception(f"未处理的服务端异常 at {request.url}")
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务器内部错误，请稍后重试或联系管理员"},
    )

# ----------------------------------------------------------------
# 前端页面入口：使用绝对路径返回 index.html
# ----------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def frontend():
    """返回前端可视化管理页面。"""
    if INDEX_HTML.is_file():
        return FileResponse(str(INDEX_HTML))
    return JSONResponse(
        status_code=404,
        content={"message": f"前端页面文件未找到，请确认 {INDEX_HTML} 存在"}
    )

# 护工端入口
@app.get("/nurse", include_in_schema=False)
async def nurse_frontend():
    """返回护工端页面。"""
    nurse_html = STATIC_DIR / "nurse.html"
    if nurse_html.is_file():
        return FileResponse(str(nurse_html))
    return JSONResponse(
        status_code=404,
        content={"message": f"护工端页面文件未找到，请确认 {nurse_html} 存在"}
    )

# 健康检查端点
@app.get("/health", tags=["Health Check"], summary="健康检查")
async def health_check():
    """提供一个简单的健康检查端点，确认服务正在运行。"""
    return {
        "status": "ok",
        "message": "智护银伴后端服务正在运行",
        "base_dir": str(BASE_DIR),
        "static_dir_exists": STATIC_DIR.is_dir(),
        "index_html_exists": INDEX_HTML.is_file()
    }


if __name__ == "__main__":
    # 开发模式热重载：仅在显式设置 RELOAD=1 时开启。
    # 生产环境（systemd / docker / uvicorn --workers N）不应启用 reload——
    # 它会和多 worker 冲突，并且每次 .py 变更都会重新加载 embedding 模型。
    reload_enabled = os.getenv("RELOAD", "0").lower() in {"1", "true", "yes"}
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=reload_enabled)
