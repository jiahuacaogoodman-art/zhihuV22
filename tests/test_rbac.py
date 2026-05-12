# -*- coding: utf-8 -*-
"""
PR #47 · RBAC 权限管理改造 · 单元/集成测试

覆盖范围
  Part A  UserStore 层
            · 权限点 + 内置角色 seed 幂等
            · admin 权限自愈（启动时强制补齐）
            · Role CRUD + 保护规则（admin.permissions / 内置删除 / 活跃用户占用）
            · get_user_permissions 缓存（TTL + invalidate）
  Part B  REST 接口
            · GET /api/auth/permissions（任意登录用户可读 + by_category）
            · GET /api/auth/roles（roles.manage 权限要求）
            · POST/PATCH/DELETE /api/auth/roles（完整 CRUD）
            · /api/auth/me 返回 permissions 列表
  Part C  require_permission 语义迁移
            · require_admin 从 "role == admin" 升级为 "拥有 users.manage"
            · 自定义角色只要勾选对应权限就能调 admin-level 接口
            · caregiver 默认勾选 ehr.write（保留老行为）
            · 权限不足返回 403 + 可读的 missing 列表
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.middleware.auth import (
    AuthTokenMiddleware,
    get_current_user,
    require_admin,
    require_permission,
)
from app.routers import auth as auth_router
from app.services.permissions import (
    ALL_PERM_KEYS,
    BUILTIN_ROLE_ADMIN,
    BUILTIN_ROLE_CAREGIVER,
    BUILTIN_ROLE_NURSE,
    PERM_EHR_AUDIT_READ,
    PERM_EHR_READ,
    PERM_EHR_WRITE,
    PERM_ROLES_MANAGE,
    PERM_USERS_MANAGE,
)
from app.services.user_store import (
    InvalidPermissionError,
    ProtectedRoleError,
    RoleKeyTakenError,
    RoleNotFoundError,
    UserStore,
    UserStoreError,
)


# =============================================================
# Part A — UserStore RBAC 单元测试
# =============================================================
class TestRbacSeedAndSync:
    """权限点 + 内置角色的启动初始化行为。"""

    def test_first_init_seeds_builtin_roles(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        roles = store.list_roles()
        role_keys = {r.role_key for r, _ in roles}
        assert role_keys == {BUILTIN_ROLE_ADMIN, BUILTIN_ROLE_NURSE, BUILTIN_ROLE_CAREGIVER}
        for role, perms in roles:
            assert role.system is True
            assert len(perms) > 0

    def test_admin_gets_all_permissions(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        fetched = store.get_role_by_key(BUILTIN_ROLE_ADMIN)
        assert fetched is not None
        _, perms = fetched
        assert set(perms) == ALL_PERM_KEYS

    def test_sync_is_idempotent(self, tmp_path):
        """重复初始化不会重复插入角色或权限点，保持稳定计数。"""
        db = tmp_path / "users.db"
        store1 = UserStore(db)
        roles_v1 = store1.list_roles()

        store2 = UserStore(db)  # 重新实例化 = 模拟重启
        roles_v2 = store2.list_roles()

        assert len(roles_v1) == len(roles_v2)
        assert {r.role_key for r, _ in roles_v1} == {r.role_key for r, _ in roles_v2}

    def test_admin_permission_self_heal(self, tmp_path):
        """手动撤掉 admin 一个权限，重启应自动补回（防锁死兜底）。"""
        db = tmp_path / "users.db"
        store1 = UserStore(db)
        # 偷偷删 admin 的 users.manage
        with store1._connect() as conn:
            conn.execute(
                "DELETE FROM role_permissions "
                "WHERE role_id = (SELECT role_id FROM roles WHERE role_key = ?) "
                "  AND perm_key = ?",
                (BUILTIN_ROLE_ADMIN, PERM_USERS_MANAGE),
            )

        store2 = UserStore(db)  # 重启
        _, perms = store2.get_role_by_key(BUILTIN_ROLE_ADMIN)
        assert PERM_USERS_MANAGE in perms
        assert set(perms) == ALL_PERM_KEYS

    def test_caregiver_keeps_ehr_write_for_compat(self, tmp_path):
        """老行为兼容：caregiver 默认勾选 ehr.write（不破坏现有部署）。"""
        store = UserStore(tmp_path / "users.db")
        _, perms = store.get_role_by_key(BUILTIN_ROLE_CAREGIVER)
        assert PERM_EHR_WRITE in perms


class TestRbacRoleCrud:
    """自定义角色 CRUD + 保护规则。"""

    def test_create_custom_role(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        role = store.create_role(
            role_key="head_nurse_finance",
            display_name="护士长兼财务",
            description="护理统筹 + 日常收费",
            permissions=[PERM_EHR_READ, PERM_EHR_WRITE],
        )
        assert role.system is False
        fetched = store.get_role_by_key("head_nurse_finance")
        assert fetched is not None
        _, perms = fetched
        assert set(perms) == {PERM_EHR_READ, PERM_EHR_WRITE}

    def test_create_duplicate_role_raises(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        store.create_role("dup", "Dup", permissions=[])
        with pytest.raises(RoleKeyTakenError):
            store.create_role("dup", "Dup2", permissions=[])

    def test_create_role_invalid_perm(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        with pytest.raises(InvalidPermissionError):
            store.create_role("bad", "Bad", permissions=["hack.everything"])

    def test_create_role_invalid_role_key(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        with pytest.raises(ValueError):
            # 包含中文 / 特殊字符
            store.create_role("财务角色", "Bad", permissions=[])
        with pytest.raises(ValueError):
            store.create_role("role-with-dash", "Bad", permissions=[])

    def test_update_role_permissions(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        store.create_role("finance", "财务", permissions=[PERM_EHR_READ])
        store.update_role(
            "finance",
            permissions=[PERM_EHR_READ, PERM_EHR_AUDIT_READ],
        )
        _, perms = store.get_role_by_key("finance")
        assert set(perms) == {PERM_EHR_READ, PERM_EHR_AUDIT_READ}

    def test_admin_permissions_protected(self, tmp_path):
        """管理员角色的权限不能通过 update_role 修改。"""
        store = UserStore(tmp_path / "users.db")
        with pytest.raises(ProtectedRoleError):
            store.update_role(BUILTIN_ROLE_ADMIN, permissions=[])
        # 但改 display_name 是允许的
        store.update_role(BUILTIN_ROLE_ADMIN, display_name="超级管理员")
        fetched = store.get_role_by_key(BUILTIN_ROLE_ADMIN)
        assert fetched[0].display_name == "超级管理员"

    def test_delete_builtin_role_protected(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        with pytest.raises(ProtectedRoleError):
            store.delete_role(BUILTIN_ROLE_NURSE)

    def test_delete_role_with_active_users_blocked(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        store.create_role("supervisor", "主管", permissions=[PERM_EHR_READ])
        store.create_user("boss", role="supervisor")
        with pytest.raises(UserStoreError) as exc_info:
            store.delete_role("supervisor")
        assert "活跃用户" in str(exc_info.value)

    def test_delete_role_after_users_deactivated(self, tmp_path):
        """停用最后一个用户后能删角色。"""
        store = UserStore(tmp_path / "users.db")
        store.create_role("temp", "临时", permissions=[])
        u = store.create_user("tempuser", role="temp")
        store.deactivate_user(u.user_id)
        store.delete_role("temp")  # 应成功
        assert store.get_role_by_key("temp") is None

    def test_delete_nonexistent_role(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        with pytest.raises(RoleNotFoundError):
            store.delete_role("ghost")


class TestRbacUserPermissions:
    """get_user_permissions 的正确性 + 缓存行为。"""

    def test_admin_user_has_all_perms(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        u = store.create_user("alice", role=BUILTIN_ROLE_ADMIN)
        perms = store.get_user_permissions(u.user_id)
        assert perms == ALL_PERM_KEYS

    def test_nurse_user_has_subset(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        u = store.create_user("bob", role=BUILTIN_ROLE_NURSE)
        perms = store.get_user_permissions(u.user_id)
        assert PERM_EHR_READ in perms
        assert PERM_EHR_WRITE in perms
        assert PERM_USERS_MANAGE not in perms
        assert PERM_ROLES_MANAGE not in perms

    def test_nonexistent_user_returns_empty_perms(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        assert store.get_user_permissions("usr_ghost") == frozenset()

    def test_inactive_user_returns_empty_perms(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        u = store.create_user("carol", role=BUILTIN_ROLE_NURSE)
        assert len(store.get_user_permissions(u.user_id)) > 0
        store.deactivate_user(u.user_id)
        assert store.get_user_permissions(u.user_id) == frozenset()

    def test_permissions_cache_is_populated(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        u = store.create_user("dave", role=BUILTIN_ROLE_NURSE)
        _ = store.get_user_permissions(u.user_id)
        cached = store._perm_cache.get(u.user_id)
        assert cached is not None

    def test_role_update_invalidates_cache(self, tmp_path):
        """改角色权限后，下一次查询应看到新权限（缓存被主动 invalidate）。"""
        store = UserStore(tmp_path / "users.db")
        store.create_role("tmp", "临时", permissions=[PERM_EHR_READ])
        u = store.create_user("eve", role="tmp")
        perms_before = store.get_user_permissions(u.user_id)
        assert perms_before == {PERM_EHR_READ}

        # 给角色加个权限
        store.update_role("tmp", permissions=[PERM_EHR_READ, PERM_EHR_WRITE])

        perms_after = store.get_user_permissions(u.user_id)
        assert perms_after == {PERM_EHR_READ, PERM_EHR_WRITE}

    def test_deactivate_user_invalidates_cache(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        u = store.create_user("frank", role=BUILTIN_ROLE_NURSE)
        _ = store.get_user_permissions(u.user_id)  # warm cache
        store.deactivate_user(u.user_id)
        # 立即查应为空（缓存已清）
        assert store.get_user_permissions(u.user_id) == frozenset()


# =============================================================
# Part B — REST 接口集成测试
# =============================================================
def _build_rbac_app(store: UserStore):
    """挂 auth 路由 + 鉴权中间件的最小 app。"""
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.responses import JSONResponse

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api")
    app.add_middleware(AuthTokenMiddleware, legacy_token="", user_store=store)
    app.state.user_store = store
    app.state.auth_mode = "user_store" if store.has_users() else "disabled"

    # 跟 main.py 保持一致：把 HTTPException.detail 包装成 {code, message}
    # 这样测试看到的响应体结构和生产一样
    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": str(exc.detail or "")},
            headers=getattr(exc, "headers", None) or None,
        )

    return app


@pytest.fixture
def rbac_store_and_admin(tmp_path):
    """(UserStore 已 seed, admin 明文 token)。"""
    store = UserStore(tmp_path / "users.db")
    admin = store.create_user("admin", role=BUILTIN_ROLE_ADMIN)
    token, _ = store.create_token(admin.user_id, label="test")
    return store, token


class TestMePermissions:
    def test_me_returns_permissions_list(self, rbac_store_and_admin):
        store, token = rbac_store_and_admin
        app = _build_rbac_app(store)
        with TestClient(app) as c:
            r = c.get("/api/auth/me", headers={"X-Auth-Token": token})
        assert r.status_code == 200
        body = r.json()
        assert "permissions" in body
        assert set(body["permissions"]) == ALL_PERM_KEYS

    def test_me_nurse_shows_subset(self, rbac_store_and_admin):
        store, _ = rbac_store_and_admin
        nurse = store.create_user("li_nurse", role=BUILTIN_ROLE_NURSE)
        nurse_token, _ = store.create_token(nurse.user_id)
        app = _build_rbac_app(store)
        with TestClient(app) as c:
            r = c.get("/api/auth/me", headers={"X-Auth-Token": nurse_token})
        assert r.status_code == 200
        perms = set(r.json()["permissions"])
        assert PERM_EHR_READ in perms
        assert PERM_USERS_MANAGE not in perms


class TestPermissionsEndpoint:
    def test_list_permissions_available_to_any_user(self, rbac_store_and_admin):
        """护工也能读权限点清单（UI 渲染必需）。"""
        store, _ = rbac_store_and_admin
        nurse = store.create_user("li", role=BUILTIN_ROLE_NURSE)
        nt, _ = store.create_token(nurse.user_id)
        app = _build_rbac_app(store)
        with TestClient(app) as c:
            r = c.get("/api/auth/permissions", headers={"X-Auth-Token": nt})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == len(ALL_PERM_KEYS)
        # 必含 by_category 分组，前端 UI 渲染 collapse 要用
        assert "by_category" in body
        assert "auth" in body["by_category"]
        assert "ehr" in body["by_category"]


class TestRolesEndpoints:
    def test_list_roles_requires_roles_manage(self, rbac_store_and_admin):
        store, admin_token = rbac_store_and_admin
        # 护士没有 roles.manage
        nurse = store.create_user("li_nurse", role=BUILTIN_ROLE_NURSE)
        nurse_token, _ = store.create_token(nurse.user_id)
        app = _build_rbac_app(store)
        with TestClient(app) as c:
            r_admin = c.get("/api/auth/roles", headers={"X-Auth-Token": admin_token})
            r_nurse = c.get("/api/auth/roles", headers={"X-Auth-Token": nurse_token})
        assert r_admin.status_code == 200
        assert r_nurse.status_code == 403
        # 403 错误信息里应包含缺失的 perm_key，便于前端展示
        assert "roles.manage" in r_nurse.json()["message"]

    def test_create_role_and_list(self, rbac_store_and_admin):
        store, admin_token = rbac_store_and_admin
        app = _build_rbac_app(store)
        h = {"X-Auth-Token": admin_token}
        with TestClient(app) as c:
            r1 = c.post(
                "/api/auth/roles",
                headers=h,
                json={
                    "role_key": "finance",
                    "display_name": "财务",
                    "description": "日常收费",
                    "permissions": [PERM_EHR_READ, PERM_EHR_AUDIT_READ],
                },
            )
            assert r1.status_code == 200
            assert r1.json()["role"]["role_key"] == "finance"

            r2 = c.get("/api/auth/roles", headers=h)
            role_keys = [r["role_key"] for r in r2.json()["roles"]]
            assert "finance" in role_keys

    def test_create_role_duplicate_returns_409(self, rbac_store_and_admin):
        store, admin_token = rbac_store_and_admin
        app = _build_rbac_app(store)
        h = {"X-Auth-Token": admin_token}
        with TestClient(app) as c:
            c.post("/api/auth/roles", headers=h, json={
                "role_key": "dup", "display_name": "D", "permissions": [],
            })
            r2 = c.post("/api/auth/roles", headers=h, json={
                "role_key": "dup", "display_name": "D2", "permissions": [],
            })
        assert r2.status_code == 409

    def test_create_role_bad_perm_returns_400(self, rbac_store_and_admin):
        store, admin_token = rbac_store_and_admin
        app = _build_rbac_app(store)
        with TestClient(app) as c:
            r = c.post("/api/auth/roles", headers={"X-Auth-Token": admin_token}, json={
                "role_key": "bad", "display_name": "B",
                "permissions": ["nonexistent.perm"],
            })
        assert r.status_code == 400

    def test_create_role_bad_role_key_format_returns_422(self, rbac_store_and_admin):
        """role_key 必须 ^[a-zA-Z0-9_]+$（Pydantic 层校验）。"""
        store, admin_token = rbac_store_and_admin
        app = _build_rbac_app(store)
        with TestClient(app) as c:
            r = c.post("/api/auth/roles", headers={"X-Auth-Token": admin_token}, json={
                "role_key": "has-dash",  # 横线非法
                "display_name": "X",
                "permissions": [],
            })
        # Pydantic 校验失败 → 422
        assert r.status_code == 422

    def test_update_role_permissions(self, rbac_store_and_admin):
        store, admin_token = rbac_store_and_admin
        app = _build_rbac_app(store)
        h = {"X-Auth-Token": admin_token}
        with TestClient(app) as c:
            c.post("/api/auth/roles", headers=h, json={
                "role_key": "supervisor", "display_name": "S",
                "permissions": [PERM_EHR_READ],
            })
            r = c.patch("/api/auth/roles/supervisor", headers=h, json={
                "permissions": [PERM_EHR_READ, PERM_EHR_WRITE],
            })
        assert r.status_code == 200
        assert set(r.json()["role"]["permissions"]) == {PERM_EHR_READ, PERM_EHR_WRITE}

    def test_update_admin_permissions_returns_400(self, rbac_store_and_admin):
        store, admin_token = rbac_store_and_admin
        app = _build_rbac_app(store)
        with TestClient(app) as c:
            r = c.patch(
                "/api/auth/roles/admin",
                headers={"X-Auth-Token": admin_token},
                json={"permissions": []},
            )
        assert r.status_code == 400
        assert "admin" in r.json()["message"]

    def test_update_admin_display_name_allowed(self, rbac_store_and_admin):
        """admin 的 display_name 可改（仅影响 UI 展示）。"""
        store, admin_token = rbac_store_and_admin
        app = _build_rbac_app(store)
        with TestClient(app) as c:
            r = c.patch(
                "/api/auth/roles/admin",
                headers={"X-Auth-Token": admin_token},
                json={"display_name": "超级管理员"},
            )
        assert r.status_code == 200

    def test_delete_builtin_role_returns_400(self, rbac_store_and_admin):
        store, admin_token = rbac_store_and_admin
        app = _build_rbac_app(store)
        with TestClient(app) as c:
            r = c.delete("/api/auth/roles/nurse", headers={"X-Auth-Token": admin_token})
        assert r.status_code == 400

    def test_delete_role_with_active_users_returns_409(self, rbac_store_and_admin):
        store, admin_token = rbac_store_and_admin
        app = _build_rbac_app(store)
        h = {"X-Auth-Token": admin_token}
        with TestClient(app) as c:
            c.post("/api/auth/roles", headers=h, json={
                "role_key": "temp", "display_name": "T", "permissions": [],
            })
            c.post("/api/auth/users", headers=h, json={
                "username": "temp_user", "role": "temp",
            })
            r = c.delete("/api/auth/roles/temp", headers=h)
        assert r.status_code == 409


class TestCreateUserAcceptsCustomRole:
    """role 校验不再写死 Literal，自定义 role_key 也能用。"""

    def test_create_user_with_custom_role(self, rbac_store_and_admin):
        store, admin_token = rbac_store_and_admin
        app = _build_rbac_app(store)
        h = {"X-Auth-Token": admin_token}
        with TestClient(app) as c:
            c.post("/api/auth/roles", headers=h, json={
                "role_key": "head_nurse_finance",
                "display_name": "护士长兼财务",
                "permissions": [PERM_EHR_READ, PERM_EHR_WRITE],
            })
            r = c.post("/api/auth/users", headers=h, json={
                "username": "zhang_head",
                "display_name": "张护士长",
                "role": "head_nurse_finance",
            })
        assert r.status_code == 200
        assert r.json()["user"]["role"] == "head_nurse_finance"

    def test_create_user_with_nonexistent_role(self, rbac_store_and_admin):
        store, admin_token = rbac_store_and_admin
        app = _build_rbac_app(store)
        with TestClient(app) as c:
            r = c.post("/api/auth/users", headers={"X-Auth-Token": admin_token}, json={
                "username": "x", "role": "ghost_role",
            })
        assert r.status_code == 400


# =============================================================
# Part C — require_permission 语义迁移验证
# =============================================================
class TestRequirePermissionSemantics:
    """核心断言：require_admin 的语义升级 —— 看权限点而不是 role 名。"""

    def test_custom_role_with_users_manage_can_call_admin_endpoints(self, tmp_path):
        """
        自定义角色勾选 users.manage → 自动能调 /api/auth/users，
        不用硬编码是不是 "admin"。
        """
        store = UserStore(tmp_path / "users.db")
        # 创建一个非 admin 的自定义角色，但授予 users.manage
        store.create_role(
            "hr_manager",
            "人事经理",
            permissions=[PERM_USERS_MANAGE],
        )
        # 顺便也得有一个 admin 用户好让我们能调 POST /roles 之外的场景
        admin = store.create_user("admin", role=BUILTIN_ROLE_ADMIN)
        _, _ = store.create_token(admin.user_id)

        hr = store.create_user("hr", role="hr_manager")
        hr_token, _ = store.create_token(hr.user_id)

        app = _build_rbac_app(store)
        with TestClient(app) as c:
            # 1) hr 可以 list_users（权限 users.manage 满足）
            r = c.get("/api/auth/users", headers={"X-Auth-Token": hr_token})
            assert r.status_code == 200

            # 2) 但 hr 不能访问需要 tokens.manage 的端点
            r = c.get("/api/auth/tokens", headers={"X-Auth-Token": hr_token})
            assert r.status_code == 403
            assert "tokens.manage" in r.json()["message"]

    def test_nurse_cannot_reach_admin_level_endpoints(self, tmp_path):
        """护士没有 users.manage，调 admin 接口应 403。"""
        store = UserStore(tmp_path / "users.db")
        admin = store.create_user("admin", role=BUILTIN_ROLE_ADMIN)
        _, _ = store.create_token(admin.user_id)
        nurse = store.create_user("nurse1", role=BUILTIN_ROLE_NURSE)
        nurse_token, _ = store.create_token(nurse.user_id)

        app = _build_rbac_app(store)
        with TestClient(app) as c:
            r = c.get("/api/auth/users", headers={"X-Auth-Token": nurse_token})
        assert r.status_code == 403

    def test_require_permission_multi_perm_and_semantics(self, tmp_path):
        """require_permission 是 AND 语义：缺任何一个都 403。"""
        store = UserStore(tmp_path / "users.db")
        store.create_role("half", "Half", permissions=[PERM_EHR_READ])  # 只给 read，不给 write
        admin = store.create_user("admin", role=BUILTIN_ROLE_ADMIN)
        _, _ = store.create_token(admin.user_id)
        u = store.create_user("half_user", role="half")
        token, _ = store.create_token(u.user_id)

        # 临时挂一个要求 read+write 两个权限的端点
        app = _build_rbac_app(store)

        @app.get("/api/test/need-both")
        def _endpoint(
            _user=Depends(require_permission(PERM_EHR_READ, PERM_EHR_WRITE)),
        ):
            return {"ok": True}

        with TestClient(app) as c:
            r = c.get("/api/test/need-both", headers={"X-Auth-Token": token})
        assert r.status_code == 403
        assert PERM_EHR_WRITE in r.json()["message"]

    def test_require_admin_is_now_permission_based(self, tmp_path):
        """
        向后兼容确认：require_admin 现在 = require_permission(users.manage)。
        内置 admin 角色拥有 users.manage → 通过；自定义角色有此权限也通过。
        """
        store = UserStore(tmp_path / "users.db")
        admin = store.create_user("admin", role=BUILTIN_ROLE_ADMIN)
        admin_token, _ = store.create_token(admin.user_id)

        app = _build_rbac_app(store)

        @app.get("/api/test/admin-only")
        def _endpoint(_user=Depends(require_admin)):
            return {"ok": True}

        with TestClient(app) as c:
            r = c.get("/api/test/admin-only", headers={"X-Auth-Token": admin_token})
        assert r.status_code == 200


class TestEhrAuditPermMigration:
    """
    /api/ehr/audit 从 require_admin 迁到 require_permission(ehr.audit_read)。
    内置 admin 仍能访问；其他角色除非被授 ehr.audit_read 权限，否则 403。
    """

    def _build_ehr_app(self, store):
        """挂 ehr 路由的最小 app（复用 test_auth.py 的做法）。"""
        from starlette.exceptions import HTTPException as StarletteHTTPException
        from starlette.responses import JSONResponse
        from app.routers import ehr

        app = FastAPI()
        app.include_router(ehr.router, prefix="/api")
        app.add_middleware(AuthTokenMiddleware, legacy_token="", user_store=store)
        app.state.user_store = store
        app.state.auth_mode = "user_store"

        @app.exception_handler(StarletteHTTPException)
        async def _http_handler(request, exc):
            return JSONResponse(
                status_code=exc.status_code,
                content={"code": exc.status_code, "message": str(exc.detail or "")},
            )

        # mock ChromaDB
        mock_col = MagicMock()
        mock_col.get.return_value = {"ids": [], "documents": [], "metadatas": []}
        ehr._get_state_backup = ehr._get_state
        ehr._get_state = lambda: (mock_col, MagicMock())
        return app, ehr

    def teardown_method(self):
        # 确保每个测试后 ehr._get_state 恢复（避免污染后续测试）
        from app.routers import ehr
        if hasattr(ehr, "_get_state_backup"):
            ehr._get_state = ehr._get_state_backup
            del ehr._get_state_backup

    def test_admin_can_read_audit(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        admin = store.create_user("admin", role=BUILTIN_ROLE_ADMIN)
        admin_token, _ = store.create_token(admin.user_id)

        app, _ = self._build_ehr_app(store)
        with TestClient(app) as c:
            r = c.get("/api/ehr/audit", headers={"X-Auth-Token": admin_token})
        assert r.status_code == 200

    def test_nurse_cannot_read_audit(self, tmp_path):
        store = UserStore(tmp_path / "users.db")
        admin = store.create_user("admin", role=BUILTIN_ROLE_ADMIN)
        store.create_token(admin.user_id)  # 保证 UserStore 非空
        nurse = store.create_user("li", role=BUILTIN_ROLE_NURSE)
        nurse_token, _ = store.create_token(nurse.user_id)

        app, _ = self._build_ehr_app(store)
        with TestClient(app) as c:
            r = c.get("/api/ehr/audit", headers={"X-Auth-Token": nurse_token})
        assert r.status_code == 403
        assert PERM_EHR_AUDIT_READ in r.json()["message"]

    def test_custom_auditor_role_can_read(self, tmp_path):
        """
        一个只授予 ehr.audit_read 的自定义角色 "auditor" 可以查审计，
        但不能做别的事——这就是 RBAC 细粒度授权的意义。
        """
        store = UserStore(tmp_path / "users.db")
        admin = store.create_user("admin", role=BUILTIN_ROLE_ADMIN)
        store.create_token(admin.user_id)
        store.create_role(
            "auditor",
            "审计员",
            permissions=[PERM_EHR_READ, PERM_EHR_AUDIT_READ],
        )
        auditor = store.create_user("zhang_auditor", role="auditor")
        auditor_token, _ = store.create_token(auditor.user_id)

        app, _ = self._build_ehr_app(store)
        with TestClient(app) as c:
            r = c.get("/api/ehr/audit", headers={"X-Auth-Token": auditor_token})
        assert r.status_code == 200
