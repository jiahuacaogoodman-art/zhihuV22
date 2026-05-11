#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
回归测试：GET /api/nursing/patient/{id} 返回前对 PII 密文解密

背景
  Chroma metadata 在写入时由 encrypt_pii_fields 加密（Fernet + "enc:" 前缀）。
  nursing.get_patient_info 曾直接把 metadata 里的字段返回给护工端，
  导致 PII 加密开启后前端看到一串密文字符串（enc:gAAAAA...），
  用户体验碎屏、且误导运维以为加密坏了。

本用例通过 patch _get_state 桩入返回 enc: 前缀数据的 mock collection，
验证接口返回的 name / bed_number 等字段是**明文**。
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_app(mock_collection):
    """组 mini app，只挂 nursing 路由；中间件放行（disabled 模式）。"""
    from app.middleware.auth import AuthTokenMiddleware
    from app.routers import nursing as nursing_mod
    from app.services.user_store import UserStore

    # patch _get_state 绕开 main.app_state 依赖
    nursing_mod._get_state_backup = nursing_mod._get_state
    nursing_mod._get_state = lambda: (mock_collection, MagicMock())

    app = FastAPI()
    app.include_router(nursing_mod.router, prefix="/api")

    # disabled 模式：空 UserStore + 空 legacy_token
    tmp_dir = tempfile.mkdtemp()
    store = UserStore(os.path.join(tmp_dir, "users.db"))
    app.add_middleware(AuthTokenMiddleware, legacy_token="", user_store=store)
    app.state.user_store = store
    app.state.auth_mode = "disabled"
    return app, nursing_mod


@pytest.fixture
def app_with_encrypted_meta(monkeypatch):
    """
    准备环境：
      · 注入真实 Fernet 密钥 → encrypt_pii_fields 会真的加密
      · 清掉 pii_crypto 模块里可能被上一个用例污染的 cipher 缓存
      · 用 encrypt_pii_fields 产出密文，再塞进 mock 的 Chroma metadata
    """
    from app.services import pii_crypto

    # 同 tests/test_pii_crypto.py：一个真正可用的 Fernet 密钥（仅测试用）
    key = "rsnsUTFhD0kHb2TLWGukQ3jV-lGGH0nODWIPGOUOkuA="
    monkeypatch.setenv("PII_ENCRYPTION_KEY", key)

    # pii_crypto 在模块加载时会懒加载 cipher；如果此前测试改过 env，
    # 需要重置模块级状态避免密钥不一致。
    # 不依赖于 pii_crypto 是否对外暴露 reset 接口：直接清掉内部缓存属性。
    for attr in ("_cipher", "_fernet", "_CIPHER"):
        if hasattr(pii_crypto, attr):
            setattr(pii_crypto, attr, None)

    # 用真实 encrypt 生成密文
    enc_name = pii_crypto.encrypt_pii_fields({"name": "张三"})["name"]
    enc_bed = pii_crypto.encrypt_pii_fields({"bed_number": "A-101"})["bed_number"]
    enc_allergy = pii_crypto.encrypt_pii_fields({"allergy": "青霉素过敏"})["allergy"]

    # 前置断言：确认此测试环境里加密是真的开的
    assert enc_name.startswith("enc:"), (
        f"前置条件失败：加密应返回 enc: 前缀，实际 {enc_name!r}。"
        " 检查 PII_ENCRYPTION_KEY 是否生效 / cryptography 是否安装。"
    )

    mock_col = MagicMock()
    mock_col.get.return_value = {
        "ids": ["p_secure_doc1"],
        "documents": ["糖尿病、高血压（明文病史文本）"],
        "metadatas": [{
            "patient_id": "p_secure",
            "name": enc_name,
            "bed_number": enc_bed,
            "allergy": enc_allergy,
            "age": 82,  # 非 PII 字段，明文直存
            "doc_type": "patient_profile",
        }],
    }
    app, mod = _build_app(mock_col)
    yield app, mod, enc_name

    # 清理：恢复 _get_state，避免污染其它测试
    mod._get_state = mod._get_state_backup


def test_get_patient_info_decrypts_pii(app_with_encrypted_meta):
    """
    回归：请求返回的 name / bed_number / allergy 必须是**明文**。

    如果路由漏掉 decrypt_pii_fields，name 等字段会以 "enc:" 密文返回，
    此断言立刻失败并暴露问题。
    """
    app, _mod, enc_name = app_with_encrypted_meta
    with TestClient(app) as c:
        r = c.get("/api/nursing/patient/p_secure")

    assert r.status_code == 200, r.text
    body = r.json()

    # 明文断言（核心回归点）
    assert body["name"] == "张三", f"name 未解密: {body['name']!r}"
    assert body["bed_number"] == "A-101", f"bed_number 未解密: {body['bed_number']!r}"
    assert body["allergy"] == "青霉素过敏", f"allergy 未解密: {body['allergy']!r}"

    # 非 PII 字段透传不变
    assert body["age"] == 82

    # medical_history 来自 document 字段，本就是明文
    assert "糖尿病" in body["medical_history"]

    # 额外保险：响应体整体不允许残留 enc: 前缀
    assert "enc:" not in r.text, f"响应里残留密文: {r.text!r}"

    # 再次确认测试环境下加密确实在工作，避免假绿
    assert enc_name.startswith("enc:")


def test_get_patient_info_404_not_leaked(app_with_encrypted_meta):
    """场景：患者不存在时返回 404，而不是被兜底成 500。"""
    _app, mod, _ = app_with_encrypted_meta
    empty_col = MagicMock()
    empty_col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
    mod._get_state = lambda: (empty_col, MagicMock())

    # 重新组 app（_get_state 是模块级函数，上面已被改写）
    app2, _ = _build_app(empty_col)
    with TestClient(app2) as c:
        r = c.get("/api/nursing/patient/nonexistent")
    assert r.status_code == 404
