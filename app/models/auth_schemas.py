# -*- coding: utf-8 -*-
"""
@File    : app/models/auth_schemas.py
@Desc    : 用户身份 + API Key 相关的 Pydantic Schema（Phase 1）

与 user_store.py 的 dataclass 的分工
  - user_store.User / ApiKeyInfo：内部持久化层的不可变数据对象（无校验、无序列化关心）
  - 本文件：对外 HTTP 接口的请求 / 响应 Schema，带字段校验、OpenAPI 文档
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# 注意：Phase 2 起 role 字段是 DB 里的 role_key，不再是编译期固定的 Literal。
# 保留 RoleLiteral 作为"内置三角色"的类型注解用（已被弃用），不再用于请求体校验。
# 具体合法性由 UserStore.is_role_valid() 运行时判断。
RoleLiteral = Literal["admin", "nurse", "caregiver"]


# ── 通用混入 ────────────────────────────────────────────
class _CodeMessage(BaseModel):
    code: int = 200
    message: Optional[str] = None


# ── User 请求 / 响应 ────────────────────────────────────
class UserResponse(BaseModel):
    """对外暴露的 User 视图，与 user_store.User.to_dict() 对齐。

    role 是 DB 里的 role_key（例如 'admin' / 'nurse' / 自定义如 'head_nurse'）；
    合法性由服务端运行时判断，schema 层不做 Literal 约束以支持自定义角色。
    """

    model_config = ConfigDict(extra="ignore")

    user_id: str
    username: str
    display_name: str
    role: str
    active: bool
    created_at: str


class CreateUserRequest(BaseModel):
    """POST /api/auth/users

    role 取 roles 表里的某个 role_key。常见值：admin / nurse / caregiver，
    或管理员在 /api/auth/roles 里创建的自定义 role_key。
    """

    username: str = Field(..., min_length=1, max_length=64)
    display_name: Optional[str] = Field(default="", max_length=128)
    role: str = Field(default="nurse", min_length=1, max_length=64)


class UserListResponse(_CodeMessage):
    total: int
    users: List[UserResponse] = Field(default_factory=list)


class CreateUserResponse(_CodeMessage):
    user: UserResponse


# ── API Key 请求 / 响应 ────────────────────────────────
class ApiKeyInfoResponse(BaseModel):
    """与 user_store.ApiKeyInfo 对齐；永远不包含 token 明文。"""

    model_config = ConfigDict(extra="ignore")

    key_id: str
    user_id: str
    username: str
    label: str
    token_prefix: str
    created_at: str
    last_used_at: str
    revoked_at: str


class CreateTokenRequest(BaseModel):
    """POST /api/auth/tokens

    label 是自由文本，便于辨认（"护工小程序"、"管理员 CLI"）。
    """

    user_id: str = Field(..., min_length=1)
    label: Optional[str] = Field(default="", max_length=128)


class CreateTokenResponse(_CodeMessage):
    """token 字段只在这一个响应里出现，且仅这一次。"""

    token: str
    key: ApiKeyInfoResponse


class TokenListResponse(_CodeMessage):
    total: int
    keys: List[ApiKeyInfoResponse] = Field(default_factory=list)


# ── /api/auth/me ──────────────────────────────────────
class MeResponse(_CodeMessage):
    """返回当前登录用户 + 鉴权是否开启等运行时元信息。"""

    authenticated: bool
    user: Optional[UserResponse] = None
    permissions: List[str] = Field(
        default_factory=list,
        description="当前用户拥有的权限点列表（前端 UI 可据此隐藏无权操作按钮）",
    )
    auth_mode: Literal["disabled", "legacy_token", "user_store"]


# ── 角色 / 权限相关 Schema（Phase 2 RBAC）─────────────
# role_key 命名规则（与 UserStore.create_role 里的校验一致）
_ROLE_KEY_PATTERN = r"^[a-zA-Z0-9_]+$"


class RoleResponse(BaseModel):
    """对外暴露的 Role 视图，与 user_store.Role.to_dict() 对齐。"""

    model_config = ConfigDict(extra="ignore")

    role_id: str
    role_key: str
    display_name: str
    description: str = ""
    system: bool = False
    created_at: str
    updated_at: str = ""
    permissions: List[str] = Field(default_factory=list)


class RoleListResponse(_CodeMessage):
    total: int
    roles: List[RoleResponse] = Field(default_factory=list)


class CreateRoleRequest(BaseModel):
    """POST /api/auth/roles"""

    role_key: str = Field(..., min_length=1, max_length=64, pattern=_ROLE_KEY_PATTERN)
    display_name: str = Field(..., min_length=1, max_length=64)
    description: Optional[str] = Field(default="", max_length=256)
    permissions: List[str] = Field(
        default_factory=list,
        description="权限点 perm_key 列表（见 GET /api/auth/permissions）",
    )


class CreateRoleResponse(_CodeMessage):
    role: RoleResponse


class UpdateRoleRequest(BaseModel):
    """PATCH /api/auth/roles/{role_key}

    任一字段为 None 即不改。permissions 传空数组表示"撤销所有权限"，
    不是"不动"——想不动就别传这个字段。
    """

    display_name: Optional[str] = Field(default=None, max_length=64)
    description: Optional[str] = Field(default=None, max_length=256)
    permissions: Optional[List[str]] = None


class UpdateRoleResponse(_CodeMessage):
    role: RoleResponse


# 权限点清单（GET /api/auth/permissions）
class PermissionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    perm_key: str
    category: str
    display_name: str
    description: str


class PermissionListResponse(_CodeMessage):
    total: int
    permissions: List[PermissionResponse] = Field(default_factory=list)
    # 按 category 分组的视图，前端 UI 渲染 collapse 时直接拿来用
    by_category: dict = Field(default_factory=dict)


__all__ = [
    "RoleLiteral",
    "UserResponse",
    "CreateUserRequest",
    "UserListResponse",
    "CreateUserResponse",
    "ApiKeyInfoResponse",
    "CreateTokenRequest",
    "CreateTokenResponse",
    "TokenListResponse",
    "MeResponse",
    # RBAC
    "RoleResponse",
    "RoleListResponse",
    "CreateRoleRequest",
    "CreateRoleResponse",
    "UpdateRoleRequest",
    "UpdateRoleResponse",
    "PermissionResponse",
    "PermissionListResponse",
]
