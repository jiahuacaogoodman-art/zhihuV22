# -*- coding: utf-8 -*-
"""
@File    : app/middleware/auth.py
@Desc    : API Key 鉴权中间件 + RBAC 权限检查依赖

鉴权模式（启动时根据配置自动判定，运行时可通过 /api/auth/me 查看）
  1. user_store   —— 有 UserStore 且至少有一个 user（常态）
                     每个请求的 token → User，注入 request.state.user
  2. legacy_token —— 没有 UserStore 或 UserStore 为空，但 AUTH_TOKEN 非空
                     单 token 匹配模式（向后兼容旧部署；bootstrap_legacy_admin
                     会把 legacy token 自动注入 UserStore 后立刻切换到 user_store）
  3. disabled     —— AUTH_TOKEN 为空 且 UserStore 为空
                     全部放行，仅限开发/测试环境；注入一个 anonymous synthetic user

受保护路径
  - /api/*      所有业务接口
  - /uploads/*  病历照片原件

永远放行
  - /           管理端主页
  - /nurse      护工端主页
  - /static/*   前端 JS / CSS / 图标
  - /health     健康检查

Token 传递
  请求头：  X-Auth-Token: <token>
  查询参数：?token=<token>   （供浏览器直接预览病历照片时使用）

权限检查（推荐用法）
  路由通过 Depends 注入当前用户 + 声明所需权限点：
    from app.middleware.auth import get_current_user, require_permission
    from app.services.permissions import PERM_EHR_WRITE

    @router.post("/ehr/patients")
    async def create_patient(
        _user: User = Depends(require_permission(PERM_EHR_WRITE)),
        ...,
    ):
        ...

  require_admin 作为兼容别名保留（等价于 require_permission("users.manage")）。
  这让自定义角色只要勾选了 users.manage 就自动具备管理员能力，不必硬编码 "admin"。
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.services.permissions import (
    PERM_ROLES_MANAGE,
    PERM_TOKENS_MANAGE,
    PERM_USERS_MANAGE,
)
from app.services.user_store import User, UserStore, ROLE_ADMIN


# ── 路径规则 ────────────────────────────────────────────
_PROTECTED_PREFIXES = ("/api/", "/uploads/")
_ALWAYS_ALLOW = ("/health",)


def _is_protected(path: str) -> bool:
    return any(path.startswith(p) for p in _PROTECTED_PREFIXES)


def _always_allow(path: str) -> bool:
    return path in _ALWAYS_ALLOW or not _is_protected(path)


# ── 合成 User：鉴权关闭 / legacy 模式用 ─────────────────
# 这两个合成 User 的 user_id 不在 UserStore 里，RBAC 权限查询会返回空集；
# require_permission 需要通过 user_id 识别它们并 bypass（见 _is_bypass_user）。
_SYNTHETIC_ANON_ID = "anon_dev"
_SYNTHETIC_LEGACY_ADMIN_ID = "legacy_admin"
_BYPASS_USER_IDS = frozenset({_SYNTHETIC_ANON_ID, _SYNTHETIC_LEGACY_ADMIN_ID})


_ANONYMOUS_USER: User = User(
    user_id=_SYNTHETIC_ANON_ID,
    username="anonymous",
    display_name="(匿名 · 开发模式)",
    role=ROLE_ADMIN,          # 与 bypass 规则保持一致：该 user 事实上拥有全权
    active=True,
    created_at="",
)


def _is_bypass_user(user: User) -> bool:
    """disabled / legacy_token 模式的合成 user，在 RBAC 体系外，直接放行。"""
    return user.user_id in _BYPASS_USER_IDS


def _extract_token(request: Request) -> str:
    return (
        request.headers.get("X-Auth-Token")
        or request.query_params.get("token")
        or ""
    )


class AuthTokenMiddleware(BaseHTTPMiddleware):
    """三模式鉴权中间件，鉴权通过后把 User 注入 request.state.user。"""

    def __init__(
        self,
        app,
        *,
        legacy_token: str = "",
        user_store: Optional[UserStore] = None,
    ):
        super().__init__(app)
        self._legacy_token = (legacy_token or "").strip()
        self._user_store = user_store

    @property
    def auth_mode(self) -> str:
        if self._user_store is not None and self._user_store.has_users():
            return "user_store"
        if self._legacy_token:
            return "legacy_token"
        return "disabled"

    def describe(self) -> dict:
        return {"auth_mode": self.auth_mode}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if _always_allow(path):
            request.state.user = _ANONYMOUS_USER
            return await call_next(request)

        mode = self.auth_mode

        if mode == "disabled":
            request.state.user = _ANONYMOUS_USER
            return await call_next(request)

        provided = _extract_token(request)
        if not provided:
            return self._unauthorized("missing token")

        if mode == "user_store":
            assert self._user_store is not None
            user = self._user_store.resolve_token_to_user(provided)
            if user is None:
                return self._unauthorized("invalid or revoked token")
            request.state.user = user
            return await call_next(request)

        # 模式 2：legacy 单 token
        if hmac.compare_digest(provided.encode(), self._legacy_token.encode()):
            request.state.user = User(
                user_id=_SYNTHETIC_LEGACY_ADMIN_ID,
                username="admin",
                display_name="Legacy AUTH_TOKEN Admin",
                role=ROLE_ADMIN,
                active=True,
                created_at="",
            )
            return await call_next(request)

        return self._unauthorized("invalid token")

    @staticmethod
    def _unauthorized(reason: str) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"code": 401, "message": "Unauthorized"},
            headers={"WWW-Authenticate": 'Bearer realm="zhihu-yinban"'},
        )


# ── FastAPI Depends helpers ────────────────────────────
def get_current_user(request: Request) -> User:
    """返回中间件注入的 User；中间件没跑过时退化为 anonymous。"""
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        return _ANONYMOUS_USER
    return user


def _get_user_permissions(request: Request, user: User) -> frozenset[str]:
    """从 request.app.state.user_store 查当前 user 的权限集合；
    合成用户或 store 未挂载时返回特殊 "*"（bypass sentinel，由调用方判断）。"""
    if _is_bypass_user(user):
        return frozenset({"*"})
    store: Optional[UserStore] = getattr(request.app.state, "user_store", None)
    if store is None:
        # 没 UserStore：意味着中间件未配 store（测试场景 / 极简启动），
        # 行为与 disabled 模式一致 —— 放行
        return frozenset({"*"})
    return store.get_user_permissions(user.user_id)


def require_permission(*perm_keys: str):
    """
    工厂：生成一个 Depends，要求当前 user 拥有**所有**给定权限点（AND 语义）。

    用法：
        @router.post(...)
        async def endpoint(_user: User = Depends(require_permission("ehr.write"))):
            ...

    行为：
      - disabled / legacy_token 模式的合成 user：直接放行
      - user_store 模式：查 UserStore.get_user_permissions（带 60s TTL 缓存）
      - 缺任何一个所需权限点 → 403，返回缺失列表便于排错
    """
    required = set(perm_keys)

    def _checker(request: Request, user: User = Depends(get_current_user)) -> User:
        granted = _get_user_permissions(request, user)
        # "*" sentinel：合成用户或无 store，bypass
        if "*" in granted:
            return user
        missing = required - granted
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"权限不足：缺少 {sorted(missing)}。"
                    f"请联系管理员在角色权限管理中授权。"
                ),
            )
        return user

    return _checker


def require_any_permission(*perm_keys: str):
    """
    OR 语义版：满足任意一个即可。
    现在代码里用不到，留着给未来那些"读或写都行"的混合接口。
    """
    candidates = set(perm_keys)

    def _checker(request: Request, user: User = Depends(get_current_user)) -> User:
        granted = _get_user_permissions(request, user)
        if "*" in granted or (granted & candidates):
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"权限不足：至少需要 {sorted(candidates)} 其中之一",
        )

    return _checker


# ── 向后兼容别名 ─────────────────────────────────────────
# require_admin 老语义：role == "admin"
# 新语义：拥有 users.manage（"管理员该干的活：管用户"）
# 内置 admin 角色天然拥有全部权限 → 语义兼容；
# 自定义角色勾选了 users.manage 也能通过 → 符合 RBAC 设计初衷。
require_admin = require_permission(PERM_USERS_MANAGE)


def require_role(*roles: str):
    """
    Deprecated：建议改用 require_permission。

    保留此工厂仅为兼容外部代码。语义与老版一致：检查 user.role 字符串。
    对合成 user（disabled / legacy）同样 bypass。
    """
    allowed = set(roles)

    def _checker(user: User = Depends(get_current_user)) -> User:
        if _is_bypass_user(user):
            return user
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：需要角色 {sorted(allowed)}，当前角色 {user.role}",
            )
        return user

    return _checker


# ── /uploads/* 预览审计中间件 ─────────────────────────────
# 为什么要单独一个中间件：
#   /uploads/* 由 Starlette StaticFiles 接管，不走 FastAPI 路由，
#   无法像 /api/* 那样用 Depends(get_current_user) + audit.log() 记录读取。
#   这里在 AuthTokenMiddleware 鉴权成功之后、StaticFiles 处理之前插一层，
#   对 2xx 响应写 RECORD_PREVIEW 审计。
class ReadAuditMiddleware(BaseHTTPMiddleware):
    """对 /uploads/* 的 GET 请求记 RECORD_PREVIEW 审计。"""

    _PREFIX = "/uploads/"

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        if (
            request.method != "GET"
            or not request.url.path.startswith(self._PREFIX)
            or not (200 <= response.status_code < 300)
        ):
            return response

        user = getattr(request.state, "user", None)
        if not isinstance(user, User):
            return response

        path_tail = request.url.path[len(self._PREFIX):]
        segments = path_tail.split("/", 1)
        patient_id = segments[0] if segments and segments[0] else ""
        filename = segments[1] if len(segments) > 1 else path_tail

        try:
            from app.services.audit_log import get_audit_log
            get_audit_log().log(
                "RECORD_PREVIEW",
                patient_id,
                user.username,
                detail=f"预览病历原件: {filename}",
            )
        except Exception as e:  # pragma: no cover - 审计失败绝不阻断下载
            logger.warning(f"RECORD_PREVIEW 审计写入失败（不影响下载）: {e}")

        return response


__all__ = [
    "AuthTokenMiddleware",
    "ReadAuditMiddleware",
    "get_current_user",
    "require_permission",
    "require_any_permission",
    "require_admin",
    "require_role",  # deprecated 但保留
]
