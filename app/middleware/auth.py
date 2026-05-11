# -*- coding: utf-8 -*-
"""
@File    : app/middleware/auth.py
@Desc    : API Key 鉴权中间件（Phase 1：用户身份 + 审计 operator 贯通）

鉴权模式（启动时根据配置自动判定，运行时可通过 /api/auth/me 查看）
  1. user_store   —— 有 UserStore 且至少有一个 user（常态）
                     每个请求的 token → User，注入 request.state.user
  2. legacy_token —— 没有 UserStore 或 UserStore 为空，但 AUTH_TOKEN 非空
                     单 token 匹配模式（向后兼容旧部署，极少出现：
                     bootstrap_legacy_admin 会把 legacy token 自动注入 UserStore）
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

FastAPI 集成
  路由层通过 Depends 注入当前用户:
    from app.middleware.auth import get_current_user, require_admin
    async def endpoint(user = Depends(get_current_user)):
        ...
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.services.user_store import User, UserStore, ROLE_ADMIN


# ── 路径规则 ────────────────────────────────────────────
_PROTECTED_PREFIXES = ("/api/", "/uploads/")
_ALWAYS_ALLOW = ("/health",)


def _is_protected(path: str) -> bool:
    return any(path.startswith(p) for p in _PROTECTED_PREFIXES)


def _always_allow(path: str) -> bool:
    return path in _ALWAYS_ALLOW or not _is_protected(path)


# ── 鉴权关闭时注入的合成 User ────────────────────────────
# 用同一个 frozen User 对象避免每次请求创建；user_id 前缀 "anon_" 便于审计辨认。
_ANONYMOUS_USER: User = User(
    user_id="anon_dev",
    username="anonymous",
    display_name="(匿名 · 开发模式)",
    role="admin",          # 鉴权关闭时本来就是无限制，这里给 admin 便于路由 require_admin 通过
    active=True,
    created_at="",
)


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
        """与 MeResponse.auth_mode 的 Literal 保持同步。"""
        if self._user_store is not None and self._user_store.has_users():
            return "user_store"
        if self._legacy_token:
            return "legacy_token"
        return "disabled"

    # 供 /auth/me 等接口读取中间件状态（避免再查一次 DB）
    def describe(self) -> dict:
        return {"auth_mode": self.auth_mode}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 不需要保护的路径：直接放行，并注入 anonymous user（路由里如果用了 Depends 仍能拿到对象）
        if _always_allow(path):
            request.state.user = _ANONYMOUS_USER
            return await call_next(request)

        mode = self.auth_mode

        # 模式 3：鉴权关闭（仅开发）
        if mode == "disabled":
            request.state.user = _ANONYMOUS_USER
            return await call_next(request)

        provided = _extract_token(request)
        if not provided:
            return self._unauthorized("missing token")

        # 模式 1：UserStore（常态）
        if mode == "user_store":
            assert self._user_store is not None
            user = self._user_store.resolve_token_to_user(provided)
            if user is None:
                return self._unauthorized("invalid or revoked token")
            request.state.user = user
            return await call_next(request)

        # 模式 2：legacy 单 token（仅当 UserStore 未初始化或还没用户时生效）
        if hmac.compare_digest(provided.encode(), self._legacy_token.encode()):
            # 合成一个 admin synthetic user，让审计日志与路由保持统一接口
            request.state.user = User(
                user_id="legacy_admin",
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
        # 响应体里不区分"未提供 / 错误 / 已吊销"，避免给攻击者信号
        return JSONResponse(
            status_code=401,
            content={"code": 401, "message": "Unauthorized"},
            headers={"WWW-Authenticate": 'Bearer realm="zhihu-yinban"'},
        )


# ── FastAPI Depends helpers ────────────────────────────
def get_current_user(request: Request) -> User:
    """
    返回中间件注入的 User。

    如果中间件没跑过（理论上不应发生：挂载顺序保证了中间件先于路由），
    退化为 anonymous，以便测试中绕过中间件仍能使用此依赖。
    """
    user = getattr(request.state, "user", None)
    if not isinstance(user, User):
        return _ANONYMOUS_USER
    return user


def require_role(*roles: str):
    """工厂：生成要求 user.role ∈ roles 的 Depends。"""
    allowed = set(roles)

    def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：需要角色 {sorted(allowed)}，当前角色 {user.role}",
            )
        return user

    return _checker


# 常用快捷依赖
require_admin = require_role(ROLE_ADMIN)


__all__ = [
    "AuthTokenMiddleware",
    "get_current_user",
    "require_role",
    "require_admin",
]
