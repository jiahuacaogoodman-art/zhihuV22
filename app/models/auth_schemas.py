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


# 与 user_store.VALID_ROLES 保持一致；Literal 让 Pydantic 自动做枚举校验。
RoleLiteral = Literal["admin", "nurse", "caregiver"]


# ── 通用混入 ────────────────────────────────────────────
class _CodeMessage(BaseModel):
    code: int = 200
    message: Optional[str] = None


# ── User 请求 / 响应 ────────────────────────────────────
class UserResponse(BaseModel):
    """对外暴露的 User 视图，与 user_store.User.to_dict() 对齐。"""

    model_config = ConfigDict(extra="ignore")

    user_id: str
    username: str
    display_name: str
    role: RoleLiteral
    active: bool
    created_at: str


class CreateUserRequest(BaseModel):
    """POST /api/auth/users"""

    username: str = Field(..., min_length=1, max_length=64)
    display_name: Optional[str] = Field(default="", max_length=128)
    role: RoleLiteral = "nurse"


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
    auth_mode: Literal["disabled", "legacy_token", "user_store"]


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
]
