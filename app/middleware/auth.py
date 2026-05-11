# -*- coding: utf-8 -*-
"""
@File    : app/middleware/auth.py
@Desc    : 轻量 X-Auth-Token 鉴权中间件

保护范围
  - /api/*      所有业务接口
  - /uploads/*  病历照片原件（不鉴权则任何人拿到 URL 就能看到病历）

不保护范围（始终放行）
  - /           管理端主页
  - /nurse      护工端主页
  - /static/*   前端 JS / CSS / 图标
  - /health     健康检查（供 systemd / k8s 探针使用）

鉴权方式
  请求头：  X-Auth-Token: <token>
  查询参数：?token=<token>   （供浏览器直接预览病历照片时使用）

配置方式
  环境变量 AUTH_TOKEN 设为非空字符串即开启；留空则鉴权关闭。

安全说明
  - Token 比较使用 hmac.compare_digest，抵御时序攻击。
  - 401 响应体不区分"未提供"和"错误"，统一返回 "Unauthorized"。
  - 中间件只做"有没有"，不做 RBAC；多角色权限须在路由层自行实现。
"""

from __future__ import annotations

import hmac
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# 需要鉴权的路径前缀
_PROTECTED_PREFIXES = ("/api/", "/uploads/")

# 始终放行（即使命中 _PROTECTED_PREFIXES 的超集也优先放行）
_ALWAYS_ALLOW = ("/health",)


def _is_protected(path: str) -> bool:
    for prefix in _PROTECTED_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _always_allow(path: str) -> bool:
    return path in _ALWAYS_ALLOW or not _is_protected(path)


class AuthTokenMiddleware(BaseHTTPMiddleware):
    """Starlette BaseHTTPMiddleware 实现：无状态 token 校验。"""

    def __init__(self, app, token: str):
        super().__init__(app)
        # 空 token → 鉴权完全关闭（开发模式）
        self._token = token.strip()

    async def dispatch(self, request: Request, call_next):
        # 鉴权未配置时直接放行
        if not self._token:
            return await call_next(request)

        path = request.url.path

        # 不需要保护的路径直接放行
        if _always_allow(path):
            return await call_next(request)

        # 从请求头或查询参数取 token
        provided = (
            request.headers.get("X-Auth-Token")
            or request.query_params.get("token")
            or ""
        )

        # 使用 compare_digest 防时序攻击
        if not hmac.compare_digest(provided.encode(), self._token.encode()):
            return JSONResponse(
                status_code=401,
                content={"code": 401, "message": "Unauthorized"},
                headers={"WWW-Authenticate": 'Bearer realm="zhihu-yinban"'},
            )

        return await call_next(request)
