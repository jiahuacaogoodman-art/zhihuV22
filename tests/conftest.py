# -*- coding: utf-8 -*-
"""
pytest 全局 fixtures。

CI 里没有 ChromaDB 数据目录、没有 Ollama、也没有 sentence-transformers 权重。
通过 monkeypatch 把所有外部依赖桩掉，让三个冒烟用例在纯 Python 环境中运行。
"""
from __future__ import annotations

import types
import sys
import numpy as np
import pytest
from unittest.mock import MagicMock, patch


# ── 桩掉重量级依赖，让 import main 不崩溃 ─────────────────────

def _stub_chromadb():
    """返回一个最小化的 chromadb stub。"""
    mock_col = MagicMock()
    mock_col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
    mock_col.query.return_value = {"ids": [[]], "documents": [[]], "metadatas": [[]]}

    mock_client = MagicMock()
    mock_client.get_or_create_collection.return_value = mock_col

    mod = types.ModuleType("chromadb")
    mod.PersistentClient = MagicMock(return_value=mock_client)
    return mod


def _stub_sentence_transformers():
    """返回一个 SentenceTransformer stub，encode() 返回零向量。"""
    class FakeModel:
        def encode(self, text, **kw):
            return np.zeros(512, dtype=np.float32)

    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = MagicMock(return_value=FakeModel())
    return mod


def _stub_requests():
    """stub requests，让 llm_service 在 CI 里能被 import 而不抛 ModuleNotFoundError。
    实际 HTTP 调用永远不会执行（TestClient 不会触发真实 LLM 路由）。"""
    mod = types.ModuleType("requests")
    mod.post = MagicMock(return_value=MagicMock(status_code=200, iter_lines=lambda: iter([])))
    mod.exceptions = types.ModuleType("requests.exceptions")
    mod.exceptions.ConnectionError = ConnectionError
    mod.exceptions.Timeout = TimeoutError
    sys.modules["requests.exceptions"] = mod.exceptions
    return mod


def _stub_pytesseract():
    mod = types.ModuleType("pytesseract")
    mod.image_to_string = MagicMock(return_value="")
    mod.TesseractNotFoundError = Exception
    return mod


def _stub_PIL():
    pil = types.ModuleType("PIL")
    img_mod = types.ModuleType("PIL.Image")
    img_mod.open = MagicMock()
    pil.Image = img_mod
    sys.modules["PIL.Image"] = img_mod
    return pil


# 在任何 import main 之前注入桩模块
sys.modules.setdefault("chromadb", _stub_chromadb())
sys.modules.setdefault("sentence_transformers", _stub_sentence_transformers())
sys.modules.setdefault("requests", _stub_requests())
sys.modules.setdefault("pytesseract", _stub_pytesseract())
sys.modules.setdefault("PIL", _stub_PIL())


# ── UserStore fixtures（Phase 1 起可用）──────────────────────
@pytest.fixture
def fresh_user_store(tmp_path):
    """
    每次用例独立的空 UserStore（tmp SQLite）。

    注意：这不是 main 模块级那个全局 user_store；业务路由里的 Depends 仍然
    走 request.app.state.user_store，测试里如果要影响业务路由行为，需要
    用 _build_test_app(fresh_user_store, ...) 组 mini app。
    """
    from app.services.user_store import UserStore
    return UserStore(tmp_path / "users.db")


@pytest.fixture
def admin_store_and_token(fresh_user_store):
    """
    返回 (UserStore, admin_token)：
      - UserStore 预置 username=admin, role=admin
      - admin_token 是明文 API Key（仅测试内使用）
    """
    admin = fresh_user_store.create_user(
        username="admin", display_name="Test Admin", role="admin"
    )
    token, _ = fresh_user_store.create_token(admin.user_id, label="test-admin-key")
    return fresh_user_store, token


@pytest.fixture(scope="session")
def client():
    """
    返回一个 FastAPI TestClient，lifespan 被跳过（不连 Chroma / 不加载模型）。

    我们用 patch 把 lifespan 换成一个空的 asynccontextmanager，
    同时把 app_state 直接注入桩数据。
    """
    from contextlib import asynccontextmanager
    import main as main_mod

    mock_col = MagicMock()
    mock_col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
    mock_col.query.return_value = {"ids": [[]], "documents": [[]], "metadatas": [[]]}

    @asynccontextmanager
    async def _noop_lifespan(app):
        main_mod.app_state["db_collection"] = mock_col
        main_mod.app_state["embedding_function"] = (
            sys.modules["sentence_transformers"].SentenceTransformer()
        )
        yield
        main_mod.app_state.clear()

    # 替换 lifespan 并重建 app（TestClient 会调用 lifespan）
    main_mod.app.router.lifespan_context = _noop_lifespan

    from fastapi.testclient import TestClient
    with TestClient(main_mod.app, raise_server_exceptions=False) as c:
        yield c
