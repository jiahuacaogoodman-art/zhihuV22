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
from loguru import logger
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


# ── /uploads/* 预览审计中间件 ─────────────────────────────
# 为什么要单独一个中间件：
#   /uploads/* 由 Starlette StaticFiles 接管，不走 FastAPI 路由，
#   无法像 /api/* 那样用 Depends(get_current_user) + audit.log() 记录读取。
#   这里在 AuthTokenMiddleware 鉴权成功之后、StaticFiles 处理之前插一层，
#   对 2xx 响应写 RECORD_PREVIEW 审计。
#
# 设计要点：
#   · 只审计成功的 2xx 响应，404/403 不污染审计表（语义：已发生的访问）
#   · 从 URL path 反推 patient_id（/uploads/<safe_patient_id>/photos/<file>）
#     失败时 patient_id 留空，operator 仍然记录
#   · 审计失败只 warning，不影响文件下载
#   · 审计实例通过 get_audit_log() 惰性获取，避免 import 时序问题
class ReadAuditMiddleware(BaseHTTPMiddleware):
    """对 /uploads/* 的 GET 请求记 RECORD_PREVIEW 审计。"""

    _PREFIX = "/uploads/"

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # 只审计：GET /uploads/* 且响应成功（2xx）
        if (
            request.method != "GET"
            or not request.url.path.startswith(self._PREFIX)
            or not (200 <= response.status_code < 300)
        ):
            return response

        # 鉴权中间件保证 /uploads/* 必然有 request.state.user；防御性兜底
        user = getattr(request.state, "user", None)
        if not isinstance(user, User):
            return response

        # 解析 patient_id：/uploads/<safe_patient_id>/photos/<filename>
        # safe_patient_id 是 _safe_filename() 的结果（可能与原始 patient_id 不完全一致），
        # 但足以审计追溯；对不上的情况留空
        path_tail = request.url.path[len(self._PREFIX):]
        segments = path_tail.split("/", 1)
        patient_id = segments[0] if segments and segments[0] else ""
        filename = segments[1] if len(segments) > 1 else path_tail

        try:
            # 惰性导入 + 惰性获取 singleton，避免循环依赖 / 启动时序问题
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
    "require_role",
    "require_admin",
]
