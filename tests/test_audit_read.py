# -*- coding: utf-8 -*-
"""
Phase 1C: 只读审计贯通测试（PR #8）

背景：医疗/养老合规场景下，"谁在什么时候查了谁的档案"和"谁改了什么"同等重要。
此前仅审计写操作（CREATE/UPDATE/DELETE/UPLOAD），本次补齐读操作：
  · PATIENT_LIST    GET  /api/ehr/patients
  · PATIENT_READ    GET  /api/ehr/patients/{id}
                    GET  /api/nursing/patient/{id}   （detail 里区分来源）
  · RECORD_READ     GET  /api/ehr/records/{patient_id}
  · DECISION_READ   GET  /api/nursing/decisions
                    GET  /api/nursing/decisions/{id}
  · RECORD_PREVIEW  GET  /uploads/*                  （由 ReadAuditMiddleware 记）

设计要点验证
  · operator 准确落到 token 对应的 username，不再是占位符
  · 404 分支不写审计（PATIENT_READ 对不存在的 id 不应留痕）
  · 失败分支不写审计（如 DECISION_READ 命中 404）
  · GET /api/ehr/audit 自身不触发审计（避免递归，该接口本身是 admin-only）
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient


# ── 共用构建器 ────────────────────────────────────────────
def _prepare_app(tmp_path, user_store):
    """组一个挂 ehr + nursing + /uploads 的最小 app，复用 audit singleton。"""
    # 先 reset audit singleton 到 tmp 路径，避免污染仓库里的 local_audit_log/
    from app.services import audit_log as audit_mod
    audit_mod.reset_audit_log()
    audit_db = tmp_path / "audit.db"
    audit_instance = audit_mod.get_audit_log(audit_db)

    # reset 之后模块级 audit 引用（ehr/nursing 都在 import 时绑定了旧 singleton）
    # 需要 monkey-patch 到新实例
    from app.routers import ehr as ehr_mod
    from app.routers import nursing as nursing_mod
    ehr_mod.audit = audit_instance
    nursing_mod.audit = audit_instance

    # mock ChromaDB collection
    mock_col = MagicMock()
    mock_col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
    embedding_fn = MagicMock()
    embedding_fn.encode.return_value = MagicMock(tolist=lambda: [0.0] * 8)

    # patch _get_state
    ehr_mod._get_state_backup = ehr_mod._get_state
    ehr_mod._get_state = lambda: (mock_col, embedding_fn)
    nursing_mod._get_state_backup = nursing_mod._get_state
    nursing_mod._get_state = lambda: (mock_col, embedding_fn)

    from app.middleware.auth import AuthTokenMiddleware, ReadAuditMiddleware

    app = FastAPI()
    app.include_router(ehr_mod.router, prefix="/api")
    app.include_router(nursing_mod.router, prefix="/api")

    # /uploads 静态目录（真文件，ReadAuditMiddleware 要能命中 200）
    uploads_dir = tmp_path / "uploads"
    (uploads_dir / "p1" / "photos").mkdir(parents=True)
    (uploads_dir / "p1" / "photos" / "sample.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")

    # 中间件顺序同 main.py：先加 Read（内层），后加 Auth（外层）
    app.add_middleware(ReadAuditMiddleware)
    app.add_middleware(
        AuthTokenMiddleware, legacy_token="", user_store=user_store
    )
    app.state.user_store = user_store
    app.state.auth_mode = "user_store" if user_store.has_users() else "disabled"

    return app, mock_col, audit_instance, ehr_mod, nursing_mod


@pytest.fixture
def env(tmp_path, admin_store_and_token):
    """预置 admin + nurse 两种身份，两套 token。"""
    store, admin_token = admin_store_and_token
    nurse = store.create_user("wang_nurse", display_name="王护士", role="nurse")
    nurse_token, _ = store.create_token(nurse.user_id)

    app, mock_col, audit_instance, ehr_mod, nursing_mod = _prepare_app(tmp_path, store)

    yield {
        "app": app,
        "mock_col": mock_col,
        "audit": audit_instance,
        "admin_token": admin_token,
        "nurse_token": nurse_token,
        "store": store,
    }

    # 清理 patch，避免污染后续测试
    ehr_mod._get_state = ehr_mod._get_state_backup
    nursing_mod._get_state = nursing_mod._get_state_backup
    from app.services import audit_log as audit_mod
    audit_mod.reset_audit_log()


def _records(audit_instance) -> list[dict]:
    """返回审计日志全量记录，按时间倒序（与 query 行为一致）。"""
    return audit_instance.query(limit=1000)


def _has_action(records, action: str, **filters) -> bool:
    for r in records:
        if r.get("action") != action:
            continue
        if all(r.get(k) == v for k, v in filters.items()):
            return True
    return False


# ── Part A：PATIENT_LIST + PATIENT_READ（ehr 入口）─────────────
class TestEhrReadAudit:
    def test_list_patients_records_audit(self, env):
        with TestClient(env["app"]) as c:
            r = c.get(
                "/api/ehr/patients",
                headers={"X-Auth-Token": env["admin_token"]},
            )
        assert r.status_code == 200
        assert isinstance(r.json(), list)

        rows = _records(env["audit"])
        assert _has_action(rows, "PATIENT_LIST", operator="admin", patient_id="")
        # detail 里带条数
        list_rows = [r for r in rows if r["action"] == "PATIENT_LIST"]
        assert any("返回" in (r.get("detail") or "") for r in list_rows)

    def test_get_nonexistent_patient_does_not_audit(self, env):
        """404 分支绝不能写审计——否则攻击者可以用失败探测污染日志。"""
        with TestClient(env["app"]) as c:
            r = c.get(
                "/api/ehr/patients/no_such_id",
                headers={"X-Auth-Token": env["admin_token"]},
            )
        assert r.status_code == 404
        rows = _records(env["audit"])
        # 不应有针对 no_such_id 的 PATIENT_READ
        assert not _has_action(rows, "PATIENT_READ", patient_id="no_such_id")

    def test_get_patient_records_audit_with_operator(self, env):
        """命中患者档案 → PATIENT_READ，operator 等于 token 对应 username。"""
        # mock collection 返回一条 patient_profile
        env["mock_col"].get.return_value = {
            "ids": ["p1_doc1"],
            "documents": ["患者信息"],
            "metadatas": [{
                "patient_id": "p1",
                "name": "张三",
                "doc_type": "patient_profile",
            }],
        }
        with TestClient(env["app"]) as c:
            r = c.get(
                "/api/ehr/patients/p1",
                headers={"X-Auth-Token": env["nurse_token"]},
            )
        assert r.status_code == 200

        rows = _records(env["audit"])
        assert _has_action(rows, "PATIENT_READ",
                           patient_id="p1", operator="wang_nurse")
        # detail 里应标注来源 = ehr
        hits = [r for r in rows if r["action"] == "PATIENT_READ"
                and r["patient_id"] == "p1"]
        assert any("ehr" in (r.get("detail") or "") for r in hits)


# ── Part B：nursing 路由的读审计 ───────────────────────────────
class TestNursingReadAudit:
    def test_nursing_patient_read_distinguishes_source(self, env):
        """护工端入口与 ehr 入口应在 detail 里区分。"""
        env["mock_col"].get.return_value = {
            "ids": ["p2_doc"],
            "documents": ["病史"],
            "metadatas": [{"patient_id": "p2", "name": "李四",
                           "doc_type": "patient_profile"}],
        }
        with TestClient(env["app"]) as c:
            r = c.get(
                "/api/nursing/patient/p2",
                headers={"X-Auth-Token": env["nurse_token"]},
            )
        assert r.status_code == 200

        rows = _records(env["audit"])
        hits = [r for r in rows if r["action"] == "PATIENT_READ"
                and r["patient_id"] == "p2"]
        assert len(hits) >= 1
        # detail 标注 nursing 来源，便于跟 ehr 入口区分
        assert any("nursing" in (r.get("detail") or "") for r in hits)

    def test_record_list_audit(self, env):
        """GET /api/ehr/records/{patient_id} 应记 RECORD_READ。"""
        env["mock_col"].get.return_value = {
            "ids": [],
            "documents": [],
            "metadatas": [],
        }
        with TestClient(env["app"]) as c:
            r = c.get(
                "/api/ehr/records/p3",
                headers={"X-Auth-Token": env["nurse_token"]},
            )
        assert r.status_code == 200
        rows = _records(env["audit"])
        assert _has_action(rows, "RECORD_READ",
                           patient_id="p3", operator="wang_nurse")

    def test_decision_list_audit(self, env):
        """GET /api/nursing/decisions 应记 DECISION_READ。"""
        env["mock_col"].get.return_value = {
            "ids": [], "documents": [], "metadatas": []
        }
        with TestClient(env["app"]) as c:
            r = c.get(
                "/api/nursing/decisions?patient_id=p4&limit=5",
                headers={"X-Auth-Token": env["admin_token"]},
            )
        assert r.status_code == 200
        rows = _records(env["audit"])
        assert _has_action(rows, "DECISION_READ",
                           patient_id="p4", operator="admin")


# ── Part C：RECORD_PREVIEW（/uploads/* 静态预览）──────────────
class TestUploadsPreviewAudit:
    def test_preview_records_audit(self, env):
        with TestClient(env["app"]) as c:
            r = c.get(
                "/uploads/p1/photos/sample.jpg",
                headers={"X-Auth-Token": env["nurse_token"]},
            )
        assert r.status_code == 200
        assert r.content.startswith(b"\xff\xd8")

        rows = _records(env["audit"])
        assert _has_action(rows, "RECORD_PREVIEW",
                           patient_id="p1", operator="wang_nurse")

    def test_preview_401_does_not_audit(self, env):
        """无 token 访问 /uploads/* → 401，不应写审计。"""
        with TestClient(env["app"]) as c:
            r = c.get("/uploads/p1/photos/sample.jpg")
        assert r.status_code == 401
        rows = _records(env["audit"])
        assert not _has_action(rows, "RECORD_PREVIEW")

    def test_preview_404_does_not_audit(self, env):
        """文件不存在 → 404，不应写审计。"""
        with TestClient(env["app"]) as c:
            r = c.get(
                "/uploads/p1/photos/nonexistent.jpg",
                headers={"X-Auth-Token": env["nurse_token"]},
            )
        assert r.status_code == 404
        rows = _records(env["audit"])
        # 不应针对 nonexistent.jpg 写审计
        preview = [r for r in rows if r["action"] == "RECORD_PREVIEW"]
        assert not any("nonexistent.jpg" in (r.get("detail") or "")
                       for r in preview)


# ── Part D：GET /api/ehr/audit 自身不审计（避免递归）──────────
class TestAuditQueryDoesNotAuditItself:
    def test_audit_query_endpoint_not_self_audited(self, env):
        """查审计日志的接口本身不应该再生成一条审计 —— 避免无限膨胀。"""
        with TestClient(env["app"]) as c:
            # 先造一条
            c.get("/api/ehr/patients",
                  headers={"X-Auth-Token": env["admin_token"]})
            before = len(_records(env["audit"]))
            # 查审计
            r = c.get("/api/ehr/audit",
                      headers={"X-Auth-Token": env["admin_token"]})
            assert r.status_code == 200
            after = len(_records(env["audit"]))
        # 查审计本身不应新增审计记录
        assert after == before
