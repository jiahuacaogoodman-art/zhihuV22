# -*- coding: utf-8 -*-
"""
冒烟测试 2：EHR 档案 CRUD 端点基础行为
- POST /api/ehr/patients  → 200 并返回 patient_id / doc_id
- GET  /api/ehr/patients  → 200 列表（可空）
- GET  /api/ehr/patients/{id} 不存在时 → 404（非 500）

不做真实向量写入（collection 已被 stub），只验证：
  路由注册正确、请求体 schema 校验、HTTP 语义不被全局 500 handler 吞掉。
"""
import pytest


def test_add_patient_returns_200(client):
    payload = {
        "patient_id": "smoke_p001",
        "name": "测试老人",
        "age": 80,
        "medical_history": "高血压、糖尿病",
    }
    r = client.post("/api/ehr/patients", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data.get("patient_id") == "smoke_p001"
    assert "doc_id" in data


def test_add_patient_missing_name_returns_422(client):
    """请求体缺少必填字段 name → 422，不能被吞成 500。"""
    r = client.post("/api/ehr/patients", json={"patient_id": "smoke_p002"})
    assert r.status_code == 422
    # 兼容 FastAPI 默认格式 {"detail": [...]} 和分层 handler 格式 {"code": 422, "errors": [...]}
    body = r.json()
    assert "detail" in body or body.get("code") == 422


def test_list_patients_returns_200(client):
    r = client.get("/api/ehr/patients")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_get_nonexistent_patient_returns_404(client):
    """不存在的 patient_id → 404，绝不能是 500。"""
    r = client.get("/api/ehr/patients/no_such_patient_xyz")
    assert r.status_code == 404
    # 兼容 FastAPI 默认格式 {"detail": "..."} 和分层 handler 格式 {"code": 404}
    body = r.json()
    assert "detail" in body or body.get("code") == 404
