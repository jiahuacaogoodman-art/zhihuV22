# -*- coding: utf-8 -*-
"""
@Time    : 2026/03/08 10:00
@Author  : jiahuaCao
@File    : config.py
@Desc    : 全局配置文件：将所有硬编码的路径、模型名称、URL等集中管理
"""

import os
from pathlib import Path

# --- 基础路径定义 ---
# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# --- ChromaDB 配置 ---
# 本地持久化存储路径
CHROMA_DB_PATH = os.path.join(BASE_DIR, "local_ehr_db")
# 集合（Collection）名称
CHROMA_COLLECTION_NAME = "elderly_ehr"

# --- 病历照片与 OCR 档案配置 ---
# 原始病历照片、OCR 文本均保存在本地目录，不上传云端。
EHR_UPLOAD_DIR = os.path.join(BASE_DIR, "local_ehr_uploads")
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "15"))
ALLOWED_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

# --- Embedding 模型配置 ---
# 使用轻量级、高效的中文向量模型，确保在无 GPU 环境下也能流畅运行
# 备选模型: 'shibing624/text2vec-base-chinese'
EMBEDDING_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
# 建议在支持 CUDA 的环境中将设备设为 'cuda'
EMBEDDING_DEVICE = "cpu"

# --- 本地大语言模型 (LLM) 配置 ---
# Ollama 服务的 API 地址
OLLAMA_API_URL = "http://localhost:11434/api/generate"
# 本地运行的模型名称
OLLAMA_MODEL_NAME = "huatuo_o1_7b"

# --- 鉴权配置 ---
# AUTH_TOKEN：所有 /api/* 和 /uploads/* 请求必须携带请求头 X-Auth-Token: <token>。
# 生产部署时务必通过环境变量设置一个长随机字符串（推荐 32+ 字符）。
# 留空 / 不设置 → 鉴权关闭（仅供开发环境，生产环境禁止留空）。
AUTH_TOKEN: str = os.getenv("AUTH_TOKEN", "")

# --- RAG Prompt 模板 ---
# 设计一个结构化的 Prompt，清晰地分离背景信息和当前问题，引导模型进行有效思考
RAG_PROMPT_TEMPLATE = (
    "你是一位经验丰富的智能护理助手，请严格根据以下信息进行分析和提供建议。\n"
    "--- 既往病史与用药记录 ---\n"
    "{retrieved_context}\n"
    "--------------------------\n"
    "--- 当前突发症状描述 ---\n"
    "{symptom}\n"
    "--------------------------\n"
    "任务要求：请综合上述所有信息，特别是患者的既往病史和过敏史，为护理人员提供一个安全、分步骤的初步处置建议。"
    "你的回答应清晰、严谨、可操作，并明确指出何时应立即联系医生。"
)
