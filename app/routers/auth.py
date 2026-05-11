# -*- coding: utf-8 -*-
"""
@File    : app/routers/auth.py
@Desc    : 用户 / API Key 管理接口（Phase 1）

接口一览
  GET    /api/auth/me                   任何已登录用户 → 当前身份 + auth_mode
  POST   /api/auth/users                admin 专属 → 新建用户
  GET    /api/auth/users                admin 专属 → 用户列表
  DELETE /api/auth/users/{user_id}      admin 专属 → 停用用户（软删 + 连带吊销其 token）
  POST   /api/auth/tokens               admin 专属 → 为指定用户签发 API Key
                                          （响应里 token 明文只出现这一次）
  GET    /api/auth/tokens                admin 专属 → Key 列表（永远不含明文）
  DELETE /api/auth/tokens/{key_id}      admin 专属 → 吊销单个 API Key

设计决策
  - UserStore 通过 app_state 注入，避免在路由内部硬引用 main 模块。
  - auth_mode 从中间件实例读取（main 启动时挂进 app_state）。
  - 500 里不回写 exception 字符串（与 main.py 的分层异常 handler 风格一致）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger

from app.middleware.auth import get_current_user, require_admin
from app.models.auth_schemas import (
    ApiKeyInfoResponse,
    CreateTokenRequest,
    CreateTokenResponse,
    CreateUserRequest,
    CreateUserResponse,
    MeResponse,
    TokenListResponse,
    UserListResponse,
    UserResponse,
)
from app.services.user_store import (
    InvalidRoleError,
    User,
    UserNotFoundError,
    UserStore,
    UserStoreError,
    UsernameTakenError,
)


router = APIRouter()


# ── UserStore 获取器 ───────────────────────────────────
def _get_user_store(request: Request) -> UserStore:
    """从 app.state 拿 UserStore；没初始化就 503。"""
    # FastAPI: request.app.state 是 Starlette State 对象，用 getattr 读。
    store = getattr(request.app.state, "user_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="UserStore 尚未初始化",
        )
    return store


def _get_auth_mode(request: Request) -> str:
    return getattr(request.app.state, "auth_mode", "disabled")


# ── /api/auth/me ──────────────────────────────────────
@router.get(
    "/auth/me",
    response_model=MeResponse,
    summary="获取当前登录用户 + 鉴权模式",
)
async def me(request: Request, user: User = Depends(get_current_user)):
    mode = _get_auth_mode(request)
    return MeResponse(
        code=200,
        authenticated=(mode != "disabled" and user.user_id not in ("anon_dev",)),
        auth_mode=mode,
        user=UserResponse(**user.to_dict()) if user else None,
    )


# ── 用户管理（admin-only）─────────────────────────────
@router.post(
    "/auth/users",
    response_model=CreateUserResponse,
    summary="新建用户（admin 专属）",
)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    _admin: User = Depends(require_admin),
):
    store = _get_user_store(request)
    try:
        new_user = store.create_user(
            username=payload.username,
            display_name=payload.display_name or "",
            role=payload.role,
        )
    except UsernameTakenError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except (InvalidRoleError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except UserStoreError as e:
        logger.error(f"UserStore 创建用户失败: {e}")
        raise HTTPException(status_code=500, detail="创建用户失败，请稍后重试")

    logger.info(f"admin='{_admin.username}' 创建用户 '{new_user.username}' role={new_user.role}")
    return CreateUserResponse(
        code=200,
        message=f"用户 {new_user.username} 已创建",
        user=UserResponse(**new_user.to_dict()),
    )


@router.get(
    "/auth/users",
    response_model=UserListResponse,
    summary="查询用户列表（admin 专属）",
)
async def list_users(
    request: Request,
    include_inactive: bool = False,
    _admin: User = Depends(require_admin),
):
    store = _get_user_store(request)
    users = store.list_users(include_inactive=include_inactive)
    return UserListResponse(
        code=200,
        total=len(users),
        users=[UserResponse(**u.to_dict()) for u in users],
    )


@router.delete(
    "/auth/users/{user_id}",
    summary="停用用户（admin 专属，软删 + 连带吊销 token）",
)
async def deactivate_user(
    user_id: str,
    request: Request,
    _admin: User = Depends(require_admin),
):
    store = _get_user_store(request)
    if user_id == _admin.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能停用自己",
        )
    try:
        store.deactivate_user(user_id)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except UserStoreError as e:
        logger.error(f"停用用户失败: {e}")
        raise HTTPException(status_code=500, detail="停用用户失败，请稍后重试")
    logger.info(f"admin='{_admin.username}' 停用用户 user_id={user_id}")
    return {"code": 200, "message": "用户已停用，其所有 API Key 同步吊销"}


# ── API Key 管理（admin-only）─────────────────────────
@router.post(
    "/auth/tokens",
    response_model=CreateTokenResponse,
    summary="为指定用户签发 API Key（admin 专属）",
)
async def create_token(
    payload: CreateTokenRequest,
    request: Request,
    _admin: User = Depends(require_admin),
):
    """
    ⚠️ token 字段明文仅在本次响应出现一次，客户端必须立刻保存；
       之后查询列表只能看到前缀（token_prefix）。
    """
    store = _get_user_store(request)
    try:
        token, info = store.create_token(
            user_id=payload.user_id,
            label=payload.label or "",
        )
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except UserStoreError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(
        f"admin='{_admin.username}' 签发 key_id={info.key_id} "
        f"user_id={info.user_id} prefix={info.token_prefix}..."
    )
    return CreateTokenResponse(
        code=200,
        message="API Key 已生成，请立即保存；本字段仅返回一次",
        token=token,
        key=ApiKeyInfoResponse(**info.__dict__),
    )


@router.get(
    "/auth/tokens",
    response_model=TokenListResponse,
    summary="查询 API Key 列表（admin 专属，永不含明文）",
)
async def list_tokens(
    request: Request,
    user_id: Optional[str] = None,
    include_revoked: bool = False,
    _admin: User = Depends(require_admin),
):
    store = _get_user_store(request)
    keys = store.list_tokens(user_id=user_id, include_revoked=include_revoked)
    return TokenListResponse(
        code=200,
        total=len(keys),
        keys=[ApiKeyInfoResponse(**k.__dict__) for k in keys],
    )


@router.delete(
    "/auth/tokens/{key_id}",
    summary="吊销 API Key（admin 专属）",
)
async def revoke_token(
    key_id: str,
    request: Request,
    _admin: User = Depends(require_admin),
):
    store = _get_user_store(request)
    try:
        store.revoke_token(key_id)
    except UserNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except UserStoreError as e:
        logger.error(f"吊销 token 失败: {e}")
        raise HTTPException(status_code=500, detail="吊销 token 失败")
    logger.info(f"admin='{_admin.username}' 吊销 key_id={key_id}")
    return {"code": 200, "message": "API Key 已吊销"}
