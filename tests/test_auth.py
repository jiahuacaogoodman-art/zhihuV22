# -*- coding: utf-8 -*-
"""
Phase 1 用户身份 + 审计 operator 贯通 · 单元/集成测试

覆盖范围
  Part A  UserStore 纯单元测试（tmp SQLite，无 FastAPI）
            · create_user / list / get / deactivate
            · create_token / list / revoke / resolve_token_to_user
            · bootstrap_legacy_admin 幂等 + 空库注入
            · has_users
  Part B  AuthTokenMiddleware + /api/auth/* 集成测试
            · 三种模式：disabled / legacy_token / user_store
            · /auth/me 返回 user + auth_mode
            · admin-only 保护：require_admin 对 nurse 角色返回 403
            · token 签发 → 用新 token 能访问 /auth/me
            · 吊销 → 立即失效
  Part C  EHR 审计 operator 贯通端到端
            · 带 admin token 调 POST /api/ehr/patients
            · 断言 audit.log 的 operator 参数 == "admin"（不再是 "api"）

测试策略
  - Part B/C 不复用 conftest.client（session scope，中间件已绑定持久 UserStore）。
    每个 fixture 用 tmp_path 起独立 UserStore + mini FastAPI app，完全隔离。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# =============================================================
# Part A — UserStore 单元测试
# =============================================================
class TestUserStore:
    def _make_store(self, tmp_path):
        from app.services.user_store import UserStore
        return UserStore(tmp_path / "users.db")

    def test_create_and_get(self, tmp_path):
        store = self._make_store(tmp_path)
        u = store.create_user("alice", display_name="Alice Admin", role="admin")
        assert u.username == "alice"
        assert u.role == "admin"
        assert u.active is True
        assert u.user_id.startswith("usr_")

        got = store.get_user(u.user_id)
        assert got is not None
        assert got.username == "alice"
        by_name = store.get_user_by_username("alice")
        assert by_name is not None
        assert by_name.user_id == u.user_id

    def test_create_user_duplicate_raises(self, tmp_path):
        from app.services.user_store import UsernameTakenError
        store = self._make_store(tmp_path)
        store.create_user("bob", role="nurse")
        with pytest.raises(UsernameTakenError):
            store.create_user("bob", role="nurse")

    def test_create_user_invalid_role(self, tmp_path):
        from app.services.user_store import InvalidRoleError
        store = self._make_store(tmp_path)
        with pytest.raises(InvalidRoleError):
            store.create_user("carol", role="root")

    def test_list_users_excludes_inactive_by_default(self, tmp_path):
        store = self._make_store(tmp_path)
        a = store.create_user("a", role="nurse")
        store.create_user("b", role="nurse")
        store.deactivate_user(a.user_id)

        active_only = [u.username for u in store.list_users()]
        assert set(active_only) == {"b"}
        all_users = [u.username for u in store.list_users(include_inactive=True)]
        assert set(all_users) == {"a", "b"}

    def test_create_token_and_resolve(self, tmp_path):
        store = self._make_store(tmp_path)
        u = store.create_user("dave", role="caregiver")
        token, info = store.create_token(u.user_id, label="test-key")
        assert token  # 明文返回一次
        assert info.token_prefix == token[:8]
        assert info.revoked_at == ""

        resolved = store.resolve_token_to_user(token)
        assert resolved is not None
        assert resolved.user_id == u.user_id
        assert resolved.username == "dave"

    def test_resolve_wrong_token_returns_none(self, tmp_path):
        store = self._make_store(tmp_path)
        u = store.create_user("eve", role="nurse")
        store.create_token(u.user_id)
        assert store.resolve_token_to_user("definitely-not-a-real-token") is None
        assert store.resolve_token_to_user("") is None

    def test_revoke_token_makes_it_invalid(self, tmp_path):
        store = self._make_store(tmp_path)
        u = store.create_user("frank", role="nurse")
        token, info = store.create_token(u.user_id)
        assert store.resolve_token_to_user(token) is not None

        store.revoke_token(info.key_id)
        assert store.resolve_token_to_user(token) is None

    def test_deactivate_user_revokes_all_tokens(self, tmp_path):
        store = self._make_store(tmp_path)
        u = store.create_user("grace", role="nurse")
        t1, _ = store.create_token(u.user_id, label="k1")
        t2, _ = store.create_token(u.user_id, label="k2")
        assert store.resolve_token_to_user(t1) is not None
        assert store.resolve_token_to_user(t2) is not None

        store.deactivate_user(u.user_id)
        assert store.resolve_token_to_user(t1) is None
        assert store.resolve_token_to_user(t2) is None

    def test_deactivate_missing_user_raises(self, tmp_path):
        from app.services.user_store import UserNotFoundError
        store = self._make_store(tmp_path)
        with pytest.raises(UserNotFoundError):
            store.deactivate_user("usr_nonexistent")

    def test_list_tokens_filters(self, tmp_path):
        store = self._make_store(tmp_path)
        u1 = store.create_user("h", role="nurse")
        u2 = store.create_user("i", role="nurse")
        _, info1 = store.create_token(u1.user_id)
        store.create_token(u1.user_id)
        store.create_token(u2.user_id)
        store.revoke_token(info1.key_id)

        assert len(store.list_tokens(user_id=u1.user_id)) == 1  # 默认不含 revoked
        assert len(store.list_tokens(user_id=u1.user_id, include_revoked=True)) == 2
        assert len(store.list_tokens(user_id=u2.user_id)) == 1

    def test_bootstrap_legacy_admin_creates_admin_when_empty(self, tmp_path):
        store = self._make_store(tmp_path)
        assert not store.has_users()
        store.bootstrap_legacy_admin("legacy-token-xyz")
        assert store.has_users()
        admin = store.get_user_by_username("admin")
        assert admin is not None
        assert admin.role == "admin"
        # legacy token 应当能解析到 admin
        resolved = store.resolve_token_to_user("legacy-token-xyz")
        assert resolved is not None
        assert resolved.username == "admin"

    def test_bootstrap_is_idempotent_when_users_exist(self, tmp_path):
        store = self._make_store(tmp_path)
        store.create_user("alice", role="admin")
        # 已有用户 → bootstrap 必须是 noop
        store.bootstrap_legacy_admin("some-token")
        assert store.get_user_by_username("admin") is None  # 没有新增 admin
        assert store.resolve_token_to_user("some-token") is None

    def test_bootstrap_empty_token_is_noop(self, tmp_path):
        store = self._make_store(tmp_path)
        store.bootstrap_legacy_admin("")
        assert not store.has_users()


# =============================================================
# Part B — 中间件 + /api/auth/* 集成
# =============================================================
def _build_test_app(user_store, legacy_token: str = ""):
    """组一个 mini FastAPI app，只挂中间件 + auth 路由。"""
    from app.middleware.auth import AuthTokenMiddleware
    from app.routers import auth as auth_router

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api")
    app.add_middleware(
        AuthTokenMiddleware,
        legacy_token=legacy_token,
        user_store=user_store,
    )

    # 路由通过 request.app.state.user_store / auth_mode 访问
    app.state.user_store = user_store
    if user_store.has_users():
        app.state.auth_mode = "user_store"
    elif legacy_token:
        app.state.auth_mode = "legacy_token"
    else:
        app.state.auth_mode = "disabled"
    return app


@pytest.fixture
def empty_store(fresh_user_store):
    """Part B 起的短名别名（转发到 conftest.fresh_user_store）。"""
    return fresh_user_store


@pytest.fixture
def store_with_admin(admin_store_and_token):
    """Part B 起的短名别名（转发到 conftest.admin_store_and_token）。"""
    return admin_store_and_token


class TestAuthModeDisabled:
    def test_disabled_allows_request_without_token(self, empty_store):
        """UserStore 空 + legacy_token 空 → disabled 模式，/auth/me 直接 200。"""
        app = _build_test_app(empty_store, legacy_token="")
        with TestClient(app) as c:
            r = c.get("/api/auth/me")
        assert r.status_code == 200
        body = r.json()
        assert body["auth_mode"] == "disabled"
        assert body["authenticated"] is False  # anonymous


class TestAuthModeLegacyToken:
    def test_legacy_token_valid(self, empty_store):
        """UserStore 空 + legacy_token 设置 → 用 legacy token 能过。"""
        app = _build_test_app(empty_store, legacy_token="leg-123")
        with TestClient(app) as c:
            r_ok = c.get("/api/auth/me", headers={"X-Auth-Token": "leg-123"})
            r_bad = c.get("/api/auth/me", headers={"X-Auth-Token": "wrong"})
            r_missing = c.get("/api/auth/me")
        assert r_ok.status_code == 200
        assert r_ok.json()["auth_mode"] == "legacy_token"
        assert r_ok.json()["user"]["username"] == "admin"
        assert r_bad.status_code == 401
        assert r_missing.status_code == 401


class TestAuthModeUserStore:
    def test_user_store_mode_and_me(self, store_with_admin):
        store, admin_token = store_with_admin
        app = _build_test_app(store, legacy_token="")
        with TestClient(app) as c:
            r = c.get("/api/auth/me", headers={"X-Auth-Token": admin_token})
        assert r.status_code == 200
        body = r.json()
        assert body["auth_mode"] == "user_store"
        assert body["authenticated"] is True
        assert body["user"]["username"] == "admin"
        assert body["user"]["role"] == "admin"

    def test_invalid_token_rejected(self, store_with_admin):
        store, _ = store_with_admin
        app = _build_test_app(store)
        with TestClient(app) as c:
            r = c.get("/api/auth/me", headers={"X-Auth-Token": "garbage"})
        assert r.status_code == 401

    def test_missing_token_rejected(self, store_with_admin):
        store, _ = store_with_admin
        app = _build_test_app(store)
        with TestClient(app) as c:
            r = c.get("/api/auth/me")
        assert r.status_code == 401

    def test_admin_can_create_user(self, store_with_admin):
        store, admin_token = store_with_admin
        app = _build_test_app(store)
        with TestClient(app) as c:
            r = c.post(
                "/api/auth/users",
                headers={"X-Auth-Token": admin_token},
                json={"username": "wang_nurse", "display_name": "王护士", "role": "nurse"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["user"]["username"] == "wang_nurse"
        assert body["user"]["role"] == "nurse"
        # 核实真的入库了
        assert store.get_user_by_username("wang_nurse") is not None

    def test_nurse_token_cannot_create_user(self, store_with_admin):
        """require_admin 应拒绝 role=nurse。"""
        store, _ = store_with_admin
        nurse = store.create_user("li_nurse", role="nurse")
        nurse_token, _ = store.create_token(nurse.user_id)
        app = _build_test_app(store)
        with TestClient(app) as c:
            r = c.post(
                "/api/auth/users",
                headers={"X-Auth-Token": nurse_token},
                json={"username": "unauthorized_new_user", "role": "nurse"},
            )
        assert r.status_code == 403

    def test_create_duplicate_user_returns_409(self, store_with_admin):
        store, admin_token = store_with_admin
        app = _build_test_app(store)
        with TestClient(app) as c:
            r1 = c.post(
                "/api/auth/users",
                headers={"X-Auth-Token": admin_token},
                json={"username": "dup", "role": "nurse"},
            )
            r2 = c.post(
                "/api/auth/users",
                headers={"X-Auth-Token": admin_token},
                json={"username": "dup", "role": "nurse"},
            )
        assert r1.status_code == 200
        assert r2.status_code == 409

    def test_token_lifecycle(self, store_with_admin):
        """签发新 token → 用新 token 访问 → 吊销 → 401。"""
        store, admin_token = store_with_admin
        nurse = store.create_user("nurse1", role="nurse")
        app = _build_test_app(store)

        with TestClient(app) as c:
            r_issue = c.post(
                "/api/auth/tokens",
                headers={"X-Auth-Token": admin_token},
                json={"user_id": nurse.user_id, "label": "mobile"},
            )
            assert r_issue.status_code == 200
            new_token = r_issue.json()["token"]
            key_id = r_issue.json()["key"]["key_id"]
            assert new_token  # 明文仅此一次

            # 用新 token 访问 /me
            r_me = c.get("/api/auth/me", headers={"X-Auth-Token": new_token})
            assert r_me.status_code == 200
            assert r_me.json()["user"]["username"] == "nurse1"

            # admin 吊销
            r_rev = c.delete(
                f"/api/auth/tokens/{key_id}",
                headers={"X-Auth-Token": admin_token},
            )
            assert r_rev.status_code == 200

            # 吊销后的 token 不再有效
            r_me_after = c.get("/api/auth/me", headers={"X-Auth-Token": new_token})
            assert r_me_after.status_code == 401

    def test_admin_cannot_deactivate_self(self, store_with_admin):
        store, admin_token = store_with_admin
        admin = store.get_user_by_username("admin")
        app = _build_test_app(store)
        with TestClient(app) as c:
            r = c.delete(
                f"/api/auth/users/{admin.user_id}",
                headers={"X-Auth-Token": admin_token},
            )
        assert r.status_code == 400
        # 确认仍然 active
        assert store.get_user(admin.user_id).active is True

    def test_token_via_query_param_works(self, store_with_admin):
        """浏览器预览 /uploads/* 时用 ?token= 也要接受。"""
        store, admin_token = store_with_admin
        app = _build_test_app(store)
        with TestClient(app) as c:
            r = c.get(f"/api/auth/me?token={admin_token}")
        assert r.status_code == 200


# =============================================================
# Part C — EHR 审计 operator 端到端
# =============================================================
class TestAuditOperatorWiring:
    """验证业务路由 audit.log() 的 operator 字段 = 当前 token 对应的 username。"""

    def _build_app_with_ehr(self, user_store, mock_col, embedding_fn):
        """组一个挂了 ehr 路由的最小 app，ChromaDB + audit 都 mock。"""
        from app.middleware.auth import AuthTokenMiddleware
        from app.routers import ehr

        app = FastAPI()
        app.include_router(ehr.router, prefix="/api")
        app.add_middleware(AuthTokenMiddleware, legacy_token="", user_store=user_store)
        app.state.user_store = user_store
        app.state.auth_mode = "user_store" if user_store.has_users() else "disabled"

        # Patch _get_state 让它返回 mock col + embedding
        ehr._get_state_backup = ehr._get_state
        ehr._get_state = lambda: (mock_col, embedding_fn)
        return app, ehr

    def test_create_patient_records_real_operator(self, tmp_path):
        """POST /api/ehr/patients 带 admin token → audit.log 第 3 参数 == "admin"。"""
        from app.services.user_store import UserStore
        store = UserStore(tmp_path / "users.db")
        admin = store.create_user("admin", role="admin")
        admin_token, _ = store.create_token(admin.user_id)

        # Mock ChromaDB collection
        mock_col = MagicMock()
        mock_col.get.return_value = {"ids": [], "documents": [], "metadatas": []}

        # Mock embedding function
        embedding_fn = MagicMock()
        embedding_fn.encode.return_value = MagicMock(tolist=lambda: [0.0] * 8)

        app, ehr_mod = self._build_app_with_ehr(store, mock_col, embedding_fn)

        # 捕获 audit.log 调用
        original_audit_log = ehr_mod.audit.log
        captured: list = []
        ehr_mod.audit.log = lambda *a, **kw: captured.append((a, kw))
        try:
            with TestClient(app) as c:
                r = c.post(
                    "/api/ehr/patients",
                    headers={"X-Auth-Token": admin_token},
                    json={"patient_id": "p_test", "name": "测试老人", "age": 80},
                )
            assert r.status_code == 200, r.text
            # 至少应记录一条 PATIENT_CREATE
            create_calls = [a for a in captured if a[0][0] == "PATIENT_CREATE"]
            assert len(create_calls) == 1
            args, _kwargs = create_calls[0]
            # audit.log(action, patient_id, operator, ...)
            assert args[2] == "admin", f"operator 应为 'admin' 而不是 {args[2]!r}"
        finally:
            ehr_mod.audit.log = original_audit_log
            ehr_mod._get_state = ehr_mod._get_state_backup

    def test_nurse_token_records_nurse_as_operator(self, tmp_path):
        """不同 token → 不同 operator。"""
        from app.services.user_store import UserStore
        store = UserStore(tmp_path / "users.db")
        admin = store.create_user("admin", role="admin")
        store.create_token(admin.user_id)
        nurse = store.create_user("wang_nurse", display_name="王护士", role="nurse")
        nurse_token, _ = store.create_token(nurse.user_id)

        mock_col = MagicMock()
        mock_col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        embedding_fn = MagicMock()
        embedding_fn.encode.return_value = MagicMock(tolist=lambda: [0.0] * 8)

        app, ehr_mod = self._build_app_with_ehr(store, mock_col, embedding_fn)
        original_audit_log = ehr_mod.audit.log
        captured: list = []
        ehr_mod.audit.log = lambda *a, **kw: captured.append((a, kw))
        try:
            with TestClient(app) as c:
                r = c.post(
                    "/api/ehr/patients",
                    headers={"X-Auth-Token": nurse_token},
                    json={"patient_id": "p_test2", "name": "王老太", "age": 85},
                )
            assert r.status_code == 200, r.text
            assert captured[0][0][2] == "wang_nurse"
        finally:
            ehr_mod.audit.log = original_audit_log
            ehr_mod._get_state = ehr_mod._get_state_backup
