# -*- coding: utf-8 -*-
"""
@File    : app/services/user_store.py
@Desc    : 用户身份 + API Key 持久化（Phase 1）

职责
  - 存储 User（username / display_name / role / active）
  - 存储 ApiKey（token 以 sha256 哈希存 DB，明文仅在签发瞬间返回一次）
  - 通过 token 明文 → User 解析（中间件调用）
  - 首次启动若 DB 为空且传入了 legacy AUTH_TOKEN，自动创建 bootstrap admin，
    保证旧部署零破坏升级

存储
  独立 SQLite 文件 local_auth/users.db（与业务库 / 审计库分离，WAL 模式）

不做什么
  - 不做 JWT、不做 session、不做密码登录（本 PR 只有 API Key）
  - 不做 RBAC 细粒度决策（中间件只做"是谁"，路由层按需调 require_role()）
  - 不做密钥过期 / 轮换自动化（下一 PR）

安全说明
  - API Key 是高熵随机字符串（32 字节 secrets.token_urlsafe → 约 43 字符）。
    对高熵值 sha256 足够，不引入 bcrypt（bcrypt 主要防低熵口令字典爆破）。
  - 比较用 hmac.compare_digest（_resolve_token_to_user 内部）抵御时序攻击。
  - token 仅在 POST /api/auth/tokens 返回的那一瞬间出现在内存和响应体里，
    之后任何查询都只能看到前缀（token_prefix）+ hash。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


# ── 角色枚举（字符串常量，避免引入额外依赖）────────────────
ROLE_ADMIN = "admin"
ROLE_NURSE = "nurse"
ROLE_CAREGIVER = "caregiver"
VALID_ROLES = frozenset({ROLE_ADMIN, ROLE_NURSE, ROLE_CAREGIVER})


# ── 不可变数据对象（对外暴露的 User 视图）───────────────────
@dataclass(frozen=True)
class User:
    user_id: str
    username: str
    display_name: str
    role: str
    active: bool
    created_at: str

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "active": self.active,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ApiKeyInfo:
    """API Key 对外展示结构，永不包含 token 明文。"""

    key_id: str
    user_id: str
    username: str
    label: str
    token_prefix: str
    created_at: str
    last_used_at: str
    revoked_at: str


# ── 异常类型：语义精确，方便路由映射成 HTTP code ───────────
class UserStoreError(Exception):
    pass


class UserNotFoundError(UserStoreError):
    pass


class UsernameTakenError(UserStoreError):
    pass


class InvalidRoleError(UserStoreError):
    pass


# ── 主存储类 ───────────────────────────────────────────────
class UserStore:
    """线程安全的用户 / Key 存储，WAL SQLite。"""

    _CREATE_SQL = """
        CREATE TABLE IF NOT EXISTS users (
            user_id      TEXT PRIMARY KEY,
            username     TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL DEFAULT '',
            role         TEXT NOT NULL,
            active       INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

        CREATE TABLE IF NOT EXISTS api_keys (
            key_id       TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            token_hash   TEXT NOT NULL UNIQUE,
            token_prefix TEXT NOT NULL DEFAULT '',   -- 前 8 位明文，便于识别
            label        TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL,
            last_used_at TEXT NOT NULL DEFAULT '',
            revoked_at   TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_keys_user ON api_keys(user_id);
        CREATE INDEX IF NOT EXISTS idx_keys_hash ON api_keys(token_hash);
    """

    def __init__(self, db_path: str | Path):
        self._path = str(db_path)
        self._lock = threading.Lock()
        self._init_db()

    # ── 初始化 ────────────────────────────────────────────
    def _init_db(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(self._CREATE_SQL)
        logger.debug(f"UserStore 初始化完成: {self._path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _hash_token(token: str) -> str:
        """API Key 是高熵随机串，sha256 足够；不用 bcrypt（慢且无收益）。"""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    # ── User CRUD ─────────────────────────────────────────
    def create_user(
        self,
        username: str,
        display_name: str = "",
        role: str = ROLE_NURSE,
    ) -> User:
        username = (username or "").strip()
        if not username:
            raise ValueError("username 不能为空")
        if role not in VALID_ROLES:
            raise InvalidRoleError(f"role 必须是 {sorted(VALID_ROLES)} 之一，收到: {role}")
        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        created_at = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO users (user_id, username, display_name, role, active, created_at) "
                    "VALUES (?, ?, ?, ?, 1, ?)",
                    (user_id, username, display_name or username, role, created_at),
                )
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as e:
                conn.execute("ROLLBACK")
                raise UsernameTakenError(f"username '{username}' 已存在") from e
        return User(
            user_id=user_id,
            username=username,
            display_name=display_name or username,
            role=role,
            active=True,
            created_at=created_at,
        )

    def list_users(self, include_inactive: bool = False) -> list[User]:
        sql = "SELECT * FROM users"
        if not include_inactive:
            sql += " WHERE active = 1"
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._row_to_user(r) for r in rows]

    def get_user(self, user_id: str) -> Optional[User]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[User]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return self._row_to_user(row) if row else None

    def deactivate_user(self, user_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("UPDATE users SET active = 0 WHERE user_id = ?", (user_id,))
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                raise UserNotFoundError(f"user_id '{user_id}' 不存在")
            # 连带吊销该 user 所有未吊销的 key
            conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE user_id = ? AND revoked_at = ''",
                (self._now(), user_id),
            )
            conn.execute("COMMIT")

    def has_users(self) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return bool(row and row["c"] > 0)

    # ── API Key CRUD ──────────────────────────────────────
    def create_token(
        self,
        user_id: str,
        label: str = "",
        *,
        explicit_token: str | None = None,
    ) -> tuple[str, ApiKeyInfo]:
        """
        为 user 签发一个新 API Key。

        Args:
          user_id: 目标用户
          label: 便于识别的标签（如"护工小程序"、"管理员 CLI"）
          explicit_token: 仅内部使用（bootstrap 时把 legacy AUTH_TOKEN 注入，
                         避免改动部署环境变量）。外部调用务必留空，由本函数生成。

        Returns:
          (plaintext_token, ApiKeyInfo)
          plaintext_token 只有这一次机会看到；之后 DB 里只有 hash。
        """
        # 先确认 user 存在且 active
        user = self.get_user(user_id)
        if user is None:
            raise UserNotFoundError(f"user_id '{user_id}' 不存在")
        if not user.active:
            raise UserStoreError(f"user '{user.username}' 已停用，不能签发 token")

        if explicit_token:
            token = explicit_token
        else:
            # 32 字节 → ~43 字符 URL-safe base64，高熵
            token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        token_prefix = token[:8]
        key_id = f"key_{uuid.uuid4().hex[:12]}"
        created_at = self._now()

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO api_keys (key_id, user_id, token_hash, token_prefix, label, "
                    "created_at, last_used_at, revoked_at) VALUES (?, ?, ?, ?, ?, ?, '', '')",
                    (key_id, user_id, token_hash, token_prefix, label, created_at),
                )
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as e:
                conn.execute("ROLLBACK")
                # hash 冲突（概率极低，但显式处理）
                raise UserStoreError(f"token 生成冲突，请重试: {e}") from e

        return token, ApiKeyInfo(
            key_id=key_id,
            user_id=user_id,
            username=user.username,
            label=label,
            token_prefix=token_prefix,
            created_at=created_at,
            last_used_at="",
            revoked_at="",
        )

    def list_tokens(
        self,
        user_id: str | None = None,
        include_revoked: bool = False,
    ) -> list[ApiKeyInfo]:
        sql = (
            "SELECT k.*, u.username AS username FROM api_keys k "
            "JOIN users u ON k.user_id = u.user_id WHERE 1 = 1"
        )
        params: list = []
        if user_id:
            sql += " AND k.user_id = ?"
            params.append(user_id)
        if not include_revoked:
            sql += " AND k.revoked_at = ''"
        sql += " ORDER BY k.created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_key_info(r) for r in rows]

    def revoke_token(self, key_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE key_id = ? AND revoked_at = ''",
                (self._now(), key_id),
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                raise UserNotFoundError(f"key_id '{key_id}' 不存在或已吊销")
            conn.execute("COMMIT")

    # ── Token → User 解析（中间件调用热路径）───────────────
    def resolve_token_to_user(self, token: str) -> Optional[User]:
        """
        输入明文 token，返回对应 active user；失败返回 None。
        同时更新 last_used_at（尽力而为，失败不影响认证结果）。
        """
        if not token:
            return None
        token_hash = self._hash_token(token)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT k.key_id, k.revoked_at, k.token_hash AS th, "
                "       u.user_id, u.username, u.display_name, u.role, u.active, u.created_at "
                "FROM api_keys k JOIN users u ON k.user_id = u.user_id "
                "WHERE k.token_hash = ?",
                (token_hash,),
            ).fetchone()
        if not row:
            return None
        # 时序安全的二次比对（防止 hash 查询被替换为别的渠道时绕过）
        if not hmac.compare_digest(row["th"], token_hash):
            return None
        if row["revoked_at"]:
            return None
        if not row["active"]:
            return None

        # 更新 last_used_at（在锁外非事务、失败忽略）
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE key_id = ?",
                    (self._now(), row["key_id"]),
                )
        except Exception as e:
            logger.debug(f"更新 last_used_at 失败（忽略）: {e}")

        return User(
            user_id=row["user_id"],
            username=row["username"],
            display_name=row["display_name"],
            role=row["role"],
            active=bool(row["active"]),
            created_at=row["created_at"],
        )

    # ── Bootstrap：兼容旧 AUTH_TOKEN 单 token 部署 ───────
    def bootstrap_legacy_admin(self, legacy_token: str) -> None:
        """
        若 DB 里还没有任何 user 且传入了 legacy AUTH_TOKEN，
        自动创建一个 username=admin 的管理员并把 legacy token 注入为其 API Key。

        这保证现有部署（只配了 AUTH_TOKEN 环境变量）升级后零破坏：
        - 继续用同一个 token 访问 /api/*
        - 审计日志里从 "api" 变成 "admin"
        - 可以调用 /api/auth/* 渐进迁移到多用户
        """
        if not legacy_token:
            return
        if self.has_users():
            logger.debug("UserStore: 已有用户，跳过 legacy bootstrap")
            return
        try:
            admin = self.create_user(
                username="admin",
                display_name="System Administrator",
                role=ROLE_ADMIN,
            )
            _, info = self.create_token(
                admin.user_id,
                label="legacy AUTH_TOKEN (bootstrap)",
                explicit_token=legacy_token,
            )
            logger.success(
                f"UserStore: bootstrap 完成 → 创建 admin 用户并绑定现有 AUTH_TOKEN "
                f"(key_prefix={info.token_prefix}...)"
            )
        except Exception as e:
            logger.error(f"UserStore bootstrap 失败（不影响启动）: {e}")

    # ── 行映射 helpers ───────────────────────────────────
    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            user_id=row["user_id"],
            username=row["username"],
            display_name=row["display_name"] or "",
            role=row["role"],
            active=bool(row["active"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_key_info(row: sqlite3.Row) -> ApiKeyInfo:
        return ApiKeyInfo(
            key_id=row["key_id"],
            user_id=row["user_id"],
            username=row["username"],
            label=row["label"] or "",
            token_prefix=row["token_prefix"] or "",
            created_at=row["created_at"],
            last_used_at=row["last_used_at"] or "",
            revoked_at=row["revoked_at"] or "",
        )
