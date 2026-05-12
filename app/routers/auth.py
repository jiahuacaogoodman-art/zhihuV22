# -*- coding: utf-8 -*-
"""
@File    : app/routers/auth.py
@Desc    : 用户 / API Key / 角色权限 管理接口

接口一览
  GET    /api/auth/me                   任何已登录用户 → 当前身份 + auth_mode + 当前权限点
  POST   /api/auth/users                users.manage  → 新建用户
  GET    /api/auth/users                users.manage  → 用户列表
  DELETE /api/auth/users/{user_id}      users.manage  → 停用用户（软删 + 连带吊销其 token）
  POST   /api/auth/tokens               tokens.manage → 为指定用户签发 API Key
                                                        （响应里 token 明文只出现这一次）
  GET    /api/auth/tokens                tokens.manage → Key 列表（永远不含明文）
  DELETE /api/auth/tokens/{key_id}      tokens.manage → 吊销单个 API Key

  GET    /api/auth/roles                roles.manage  → 全部角色 + 每个角色的权限点
  POST   /api/auth/roles                roles.manage  → 创建自定义角色
  PATCH  /api/auth/roles/{role_key}     roles.manage  → 修改角色（admin 的 permissions 受保护）
  DELETE /api/auth/roles/{role_key}     roles.manage  → 删除自定义角色（有活跃用户则 409）
  GET    /api/auth/permissions          登录即可      → 全部权限点清单（UI 渲染用）

权限点：
  users.manage   —— 用户创建/停用/列表
  tokens.manage  —— API Key 签发/吊销/列表
  roles.manage   —— 角色权限配置（高风险，默认只授予内置 admin）
  内置 admin 角色自动拥有全部权限点；自定义角色可以只勾选其中一部分，
  实现"能发 Token 但不能建用户"这样的细粒度授权。

设计决策
  - UserStore 通过 app_state 注入，避免在路由内部硬引用 main 模块。
  - auth_mode 从中间件实例读取（main 启动时挂进 app_state）。
  - 500 里不回写 exception 字符串（与 main.py 的分层异常 handler 风格一致）。
  - admin 角色权限硬保护：UserStore + 路由层双保险，禁止撤销其任何权限。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger

from app.middleware.auth import get_current_user, require_permission
from app.services.permissions import (
    PERM_ROLES_MANAGE,
    PERM_TOKENS_MANAGE,
    PERM_USERS_MANAGE,
    permissions_by_category,
    ALL_PERMISSIONS,
)
from app.models.auth_schemas import (
    ApiKeyInfoResponse,
    CreateRoleRequest,
    CreateRoleResponse,
    CreateTokenRequest,
    CreateTokenResponse,
    CreateUserRequest,
    CreateUserResponse,
    MeResponse,
    PermissionListResponse,
    PermissionResponse,
    RoleListResponse,
    RoleResponse,
    TokenListResponse,
    UpdateRoleRequest,
    UpdateRoleResponse,
    UserListResponse,
    UserResponse,
)
from app.services.user_store import (
    InvalidPermissionError,
    InvalidRoleError,
    ProtectedRoleError,
    Role,
    RoleKeyTakenError,
    RoleNotFoundError,
    User,
    UserNotFoundError,
    UserStore,
    UserStoreError,
    UsernameTakenError,
)


router = APIRouter()


# 合成匿名 user 的 id（与 middleware/auth.py 保持一致）
# 用于区分"真登录用户"与"disabled 模式下的合成 anonymous"
_SYNTHETIC_ANON_IDS = frozenset({"anon_dev", "legacy_admin"})


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
    # 把当前用户的权限点顺手返回，前端能据此隐藏无权操作的按钮
    store: Optional[UserStore] = getattr(request.app.state, "user_store", None)
    if user.user_id in _SYNTHETIC_ANON_IDS or store is None:
        # 合成 user（disabled / legacy）天然有全权，用 "*" 表意而不是展开成所有 perm
        # 这样前端可以区分"真的被授权了"和"鉴权关着无限制"
        permissions = ["*"]
    else:
        permissions = sorted(store.get_user_permissions(user.user_id))
    return MeResponse(
        code=200,
        authenticated=(mode != "disabled" and user.user_id not in _SYNTHETIC_ANON_IDS),
        auth_mode=mode,
        user=UserResponse(**user.to_dict()) if user else None,
        permissions=permissions,
    )


# ── 角色管理（需 roles.manage 权限）──────────────────
# 为什么路由层还要保留 role_key == "admin" 的硬保护，
# 即使 UserStore 里已经在 DB 层 ProtectedRoleError 了？
#   - 双保险：UI 可能直接调 PATCH，UserStore 层是最后一道
#   - 路由层能在 Schema 校验前拦截，错误信息对前端更友好

def _role_to_response(role: Role, permissions: list[str]) -> RoleResponse:
    return RoleResponse(
        role_id=role.role_id,
        role_key=role.role_key,
        display_name=role.display_name,
        description=role.description,
        system=role.system,
        created_at=role.created_at,
        updated_at=role.updated_at,
        permissions=sorted(permissions),
    )


@router.get(
    "/auth/roles",
    response_model=RoleListResponse,
    summary="查询所有角色 + 每个角色的权限点（需要 roles.manage 权限）",
)
async def list_roles(
    request: Request,
    _admin: User = Depends(require_permission(PERM_ROLES_MANAGE)),
):
    store = _get_user_store(request)
    roles = store.list_roles()
    return RoleListResponse(
        code=200,
        total=len(roles),
        roles=[_role_to_response(r, perms) for r, perms in roles],
    )


@router.post(
    "/auth/roles",
    response_model=CreateRoleResponse,
    summary="创建自定义角色（需要 roles.manage 权限）",
)
async def create_role(
    payload: CreateRoleRequest,
    request: Request,
    _admin: User = Depends(require_permission(PERM_ROLES_MANAGE)),
):
    store = _get_user_store(request)
    try:
        role = store.create_role(
            role_key=payload.role_key,
            display_name=payload.display_name,
            description=payload.description or "",
            permissions=payload.permissions or (),
        )
    except RoleKeyTakenError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except (InvalidPermissionError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except UserStoreError as e:
        logger.error(f"创建角色失败: {e}")
        raise HTTPException(status_code=500, detail="创建角色失败，请稍后重试")

    # 拉一次完整视图（含刚写入的 permissions）
    fetched = store.get_role_by_key(role.role_key)
    assert fetched is not None
    logger.info(f"admin='{_admin.username}' 创建自定义角色 role_key='{role.role_key}'")
    return CreateRoleResponse(
        code=200,
        message=f"角色 {role.role_key} 已创建",
        role=_role_to_response(*fetched),
    )


@router.patch(
    "/auth/roles/{role_key}",
    response_model=UpdateRoleResponse,
    summary="修改角色（需要 roles.manage 权限）",
)
async def update_role(
    role_key: str,
    payload: UpdateRoleRequest,
    request: Request,
    _admin: User = Depends(require_permission(PERM_ROLES_MANAGE)),
):
    """
    display_name / description / permissions 独立可改。

    admin 角色限制：
      - display_name、description 可改（仅影响 UI 展示）
      - permissions 不可改（路由层先拦截，给前端 400 更友好）
    """
    if role_key == "admin" and payload.permissions is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="admin 角色的权限受保护，不可修改（防锁死）",
        )
    store = _get_user_store(request)
    try:
        store.update_role(
            role_key=role_key,
            display_name=payload.display_name,
            description=payload.description,
            permissions=payload.permissions,
        )
    except RoleNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ProtectedRoleError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except (InvalidPermissionError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except UserStoreError as e:
        logger.error(f"更新角色失败: {e}")
        raise HTTPException(status_code=500, detail="更新角色失败，请稍后重试")

    fetched = store.get_role_by_key(role_key)
    assert fetched is not None
    logger.info(f"admin='{_admin.username}' 更新角色 role_key='{role_key}'")
    return UpdateRoleResponse(
        code=200,
        message=f"角色 {role_key} 已更新",
        role=_role_to_response(*fetched),
    )


@router.delete(
    "/auth/roles/{role_key}",
    summary="删除自定义角色（需要 roles.manage 权限）",
)
async def delete_role(
    role_key: str,
    request: Request,
    _admin: User = Depends(require_permission(PERM_ROLES_MANAGE)),
):
    """
    只能删自定义角色。保护规则：
      - 内置角色（admin / nurse / caregiver）→ 400
      - 尚有活跃用户使用 → 400，提示先转岗或停用
    """
    store = _get_user_store(request)
    try:
        store.delete_role(role_key)
    except RoleNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ProtectedRoleError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except UserStoreError as e:
        # 使用中、或其他业务冲突
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    logger.info(f"admin='{_admin.username}' 删除角色 role_key='{role_key}'")
    return {"code": 200, "message": f"角色 {role_key} 已删除"}


# ── 权限点清单（任意已登录用户都能读，用于 UI 渲染角色编辑器）────
@router.get(
    "/auth/permissions",
    response_model=PermissionListResponse,
    summary="查询全部权限点（任意已登录用户可读）",
)
async def list_permissions(
    _user: User = Depends(get_current_user),
):
    """
    权限点清单是应用元信息、不含敏感数据，任意登录用户可读
    （前端"角色管理"页面要渲染权限勾选框，设计上它只对有 roles.manage 的
    管理员显示，所以这里不单独加权限控制；如需收紧可改成 require_permission(PERM_ROLES_MANAGE)）。
    """
    return PermissionListResponse(
        code=200,
        total=len(ALL_PERMISSIONS),
        permissions=[PermissionResponse(**p.to_dict()) for p in ALL_PERMISSIONS],
        by_category=permissions_by_category(),
    )


# ── 用户管理（admin-only）─────────────────────────────
@router.post(
    "/auth/users",
    response_model=CreateUserResponse,
    summary="新建用户（需要 users.manage 权限）",
)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    _admin: User = Depends(require_permission(PERM_USERS_MANAGE)),
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
    summary="查询用户列表（需要 users.manage 权限）",
)
async def list_users(
    request: Request,
    include_inactive: bool = False,
    _admin: User = Depends(require_permission(PERM_USERS_MANAGE)),
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
    summary="停用用户（需要 users.manage 权限，软删 + 连带吊销 token）",
)
async def deactivate_user(
    user_id: str,
    request: Request,
    _admin: User = Depends(require_permission(PERM_USERS_MANAGE)),
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
    summary="为指定用户签发 API Key（需要 tokens.manage 权限）",
)
async def create_token(
    payload: CreateTokenRequest,
    request: Request,
    _admin: User = Depends(require_permission(PERM_TOKENS_MANAGE)),
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
    summary="查询 API Key 列表（需要 tokens.manage 权限，永不含明文）",
)
async def list_tokens(
    request: Request,
    user_id: Optional[str] = None,
    include_revoked: bool = False,
    _admin: User = Depends(require_permission(PERM_TOKENS_MANAGE)),
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
    summary="吊销 API Key（需要 tokens.manage 权限）",
)
async def revoke_token(
    key_id: str,
    request: Request,
    _admin: User = Depends(require_permission(PERM_TOKENS_MANAGE)),
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
