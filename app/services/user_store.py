# -*- coding: utf-8 -*-
"""
@File    : app/services/user_store.py
@Desc    : 用户身份 + API Key + RBAC 角色权限持久化

职责
  - 存储 User（username / display_name / role / active）
  - 存储 ApiKey（token 以 sha256 哈希存 DB，明文仅在签发瞬间返回一次）
  - 存储 Role（内置 + 自定义）+ Role↔Permission 关系
  - 通过 token 明文 → User 解析（中间件调用）
  - 首次启动若 DB 为空且传入了 legacy AUTH_TOKEN，自动创建 bootstrap admin，
    保证旧部署零破坏升级
  - 启动时 sync 权限点清单 + seed 内置角色

存储
  独立 SQLite 文件 local_auth/users.db（与业务库 / 审计库分离，WAL 模式）

不做什么
  - 不做 JWT、不做 session、不做密码登录
  - 不做行级权限（"只能看 A 楼的老人"那种），只有模块级权限点
  - 不做密钥过期 / 轮换自动化

安全说明
  - API Key 是高熵随机串（32 字节 secrets.token_urlsafe → 约 43 字符），sha256 足够。
  - 比较用 hmac.compare_digest 抵御时序攻击。
  - token 仅在 POST /api/auth/tokens 返回时明文出现一次，之后只有 hash + 前缀。
  - admin 角色在代码层硬保护：不能删、不能撤销 roles.manage 权限（防锁死）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

from app.services.permissions import (
    ALL_PERMISSIONS,
    ALL_PERM_KEYS,
    BUILTIN_ROLE_ADMIN,
    BUILTIN_ROLES,
    PERM_ROLES_MANAGE,
    expand_builtin_perms,
    is_valid_perm_key,
)


# ── 向后兼容：保留原三个常量 + VALID_ROLES ────────────────
# 这些常量现在是"初始内置角色的 key"而不是"全部合法 role"的穷举；
# 校验角色是否合法改为查 roles 表（见 UserStore.is_role_valid）。
ROLE_ADMIN = "admin"
ROLE_NURSE = "nurse"
ROLE_CAREGIVER = "caregiver"

#: 已废弃：保留导出以防外部代码引用；实际角色校验走 DB
#: （改造完成后若无外部依赖可以安全删除）
VALID_ROLES = frozenset({ROLE_ADMIN, ROLE_NURSE, ROLE_CAREGIVER})


# ── 不可变数据对象 ────────────────────────────────────────
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


@dataclass(frozen=True)
class Role:
    """角色定义：内置 system=True 不可删不可改，自定义可管理。"""

    role_id: str
    role_key: str            # 唯一标识（英文，给代码引用）
    display_name: str        # 中文名（给 UI 展示）
    description: str
    system: bool             # 内置角色标记
    created_at: str
    updated_at: str

    def to_dict(self, permissions: Optional[list[str]] = None) -> dict:
        d = {
            "role_id": self.role_id,
            "role_key": self.role_key,
            "display_name": self.display_name,
            "description": self.description,
            "system": self.system,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if permissions is not None:
            d["permissions"] = sorted(permissions)
        return d


# ── 异常类型 ──────────────────────────────────────────────
class UserStoreError(Exception):
    pass


class UserNotFoundError(UserStoreError):
    pass


class UsernameTakenError(UserStoreError):
    pass


class InvalidRoleError(UserStoreError):
    pass


class RoleNotFoundError(UserStoreError):
    pass


class RoleKeyTakenError(UserStoreError):
    pass


class ProtectedRoleError(UserStoreError):
    """admin 角色受保护，禁止删除 / 禁止撤销 roles.manage。"""


class InvalidPermissionError(UserStoreError):
    pass


# ── 权限缓存 ──────────────────────────────────────────────
# 中间件在热路径每次请求都要查"user → permissions"，
# 查两张表太贵。用带 TTL 的内存缓存抵挡。
#
# 设计：
#   - 键 = user_id
#   - 值 = (perms: frozenset[str], expires_at: float)
#   - TTL 60 秒：改权限后最多 60 秒内并发请求仍用旧权限
#   - 改角色 / 改 role_permissions 时主动调 invalidate() 让缓存失效
# 对小机构规模（< 100 用户）这个策略足够安全，且不引入第三方缓存依赖。
_PERM_CACHE_TTL_SECONDS = 60.0


class _PermissionCache:
    """线程安全、带 TTL 的用户权限缓存。"""

    def __init__(self, ttl: float = _PERM_CACHE_TTL_SECONDS):
        self._ttl = ttl
        self._data: dict[str, tuple[frozenset[str], float]] = {}
        self._lock = threading.Lock()

    def get(self, user_id: str) -> Optional[frozenset[str]]:
        with self._lock:
            entry = self._data.get(user_id)
            if entry is None:
                return None
            perms, expires_at = entry
            if time.monotonic() >= expires_at:
                self._data.pop(user_id, None)
                return None
            return perms

    def set(self, user_id: str, perms: Iterable[str]) -> None:
        with self._lock:
            self._data[user_id] = (
                frozenset(perms),
                time.monotonic() + self._ttl,
            )

    def invalidate_user(self, user_id: str) -> None:
        with self._lock:
            self._data.pop(user_id, None)

    def invalidate_all(self) -> None:
        with self._lock:
            self._data.clear()


# ── 主存储类 ──────────────────────────────────────────────
class UserStore:
    """线程安全的用户 / Key / Role 存储，WAL SQLite。"""

    # 注意：CREATE TABLE IF NOT EXISTS 让老库升级时自动加新表，不影响已有数据
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
            token_prefix TEXT NOT NULL DEFAULT '',
            label        TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL,
            last_used_at TEXT NOT NULL DEFAULT '',
            revoked_at   TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_keys_user ON api_keys(user_id);
        CREATE INDEX IF NOT EXISTS idx_keys_hash ON api_keys(token_hash);

        -- RBAC：角色定义
        CREATE TABLE IF NOT EXISTS roles (
            role_id      TEXT PRIMARY KEY,
            role_key     TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            description  TEXT NOT NULL DEFAULT '',
            system       INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_roles_key ON roles(role_key);

        -- RBAC：权限点清单（由代码 sync，不由运维手改）
        CREATE TABLE IF NOT EXISTS permissions (
            perm_key     TEXT PRIMARY KEY,
            category     TEXT NOT NULL,
            display_name TEXT NOT NULL,
            description  TEXT NOT NULL DEFAULT ''
        );

        -- RBAC：角色 ↔ 权限多对多
        CREATE TABLE IF NOT EXISTS role_permissions (
            role_id      TEXT NOT NULL,
            perm_key     TEXT NOT NULL,
            PRIMARY KEY (role_id, perm_key),
            FOREIGN KEY (role_id) REFERENCES roles(role_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_role_perms_role ON role_permissions(role_id);
    """

    def __init__(self, db_path: str | Path):
        self._path = str(db_path)
        self._lock = threading.Lock()
        self._perm_cache = _PermissionCache()
        self._init_db()
        # 启动即同步权限点 + seed 内置角色
        # 幂等：新库里 seed 三个内置角色；老库里只新增没声明过的权限点
        self._sync_permissions_and_builtin_roles()

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
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    # ── 权限点 + 内置角色 sync（启动时幂等执行）────────────
    def _sync_permissions_and_builtin_roles(self) -> None:
        """
        同步代码里声明的权限点和内置角色到 DB。

        行为：
          - permissions 表：UPSERT 每个 ALL_PERMISSIONS 条目（perm_key 冲突则更新 display_name / description）；
            不清理已废弃的 perm_key，避免删代码时意外撤销管理员正在用的权限（走显式 migration 清理）。
          - roles 表：三个内置角色的 role_key 不存在时插入；存在则跳过（不覆盖用户已修改的权限）。
          - role_permissions 表：仅在角色是"新建"时设默认权限；已有角色不动。

        admin 角色例外：每次启动都强制修复其权限 = 全部权限点，防止管理员误改把自己锁死。
        """
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # 1. Permissions: upsert
                for p in ALL_PERMISSIONS:
                    conn.execute(
                        "INSERT INTO permissions (perm_key, category, display_name, description) "
                        "VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(perm_key) DO UPDATE SET "
                        "    category = excluded.category, "
                        "    display_name = excluded.display_name, "
                        "    description = excluded.description",
                        (p.perm_key, p.category, p.display_name, p.description),
                    )

                # 2. 内置角色：不存在则插入（role_permissions 跟着 seed 一次）
                for role_key, display_name in BUILTIN_ROLES.items():
                    row = conn.execute(
                        "SELECT role_id FROM roles WHERE role_key = ?",
                        (role_key,),
                    ).fetchone()
                    if row is None:
                        role_id = f"role_{role_key}" if role_key in BUILTIN_ROLES else f"role_{uuid.uuid4().hex[:12]}"
                        conn.execute(
                            "INSERT INTO roles (role_id, role_key, display_name, description, system, "
                            "                    created_at, updated_at) "
                            "VALUES (?, ?, ?, ?, 1, ?, ?)",
                            (role_id, role_key, display_name,
                             f"系统内置角色: {display_name}", now, now),
                        )
                        # 写默认权限
                        for perm_key in sorted(expand_builtin_perms(role_key)):
                            conn.execute(
                                "INSERT OR IGNORE INTO role_permissions (role_id, perm_key) "
                                "VALUES (?, ?)",
                                (role_id, perm_key),
                            )
                        logger.info(f"UserStore: seed 内置角色 '{role_key}' ({display_name})")

                # 3. admin 权限自愈：每次启动都把 admin 权限重置为全部
                #    这是防锁死兜底——即便有人在 UI 或 SQL 里意外撤销了 admin 的权限
                admin_row = conn.execute(
                    "SELECT role_id FROM roles WHERE role_key = ?",
                    (BUILTIN_ROLE_ADMIN,),
                ).fetchone()
                if admin_row is not None:
                    admin_role_id = admin_row["role_id"]
                    for perm_key in sorted(ALL_PERM_KEYS):
                        conn.execute(
                            "INSERT OR IGNORE INTO role_permissions (role_id, perm_key) "
                            "VALUES (?, ?)",
                            (admin_role_id, perm_key),
                        )

                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        # sync 后缓存必须全体失效（有可能新加了 perm）
        self._perm_cache.invalidate_all()

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
        # 从 DB 校验 role 是否合法（不再用硬编码 VALID_ROLES）
        if not self.is_role_valid(role):
            raise InvalidRoleError(
                f"role '{role}' 不是合法角色，请先通过 /api/auth/roles 创建或使用内置角色"
            )
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
            conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE user_id = ? AND revoked_at = ''",
                (self._now(), user_id),
            )
            conn.execute("COMMIT")
        # 停用用户 = 权限收回；缓存立即失效
        self._perm_cache.invalidate_user(user_id)

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
        user = self.get_user(user_id)
        if user is None:
            raise UserNotFoundError(f"user_id '{user_id}' 不存在")
        if not user.active:
            raise UserStoreError(f"user '{user.username}' 已停用，不能签发 token")

        if explicit_token:
            token = explicit_token
        else:
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

    # ── Token → User 解析（中间件热路径）───────────────────
    def resolve_token_to_user(self, token: str) -> Optional[User]:
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
        if not hmac.compare_digest(row["th"], token_hash):
            return None
        if row["revoked_at"]:
            return None
        if not row["active"]:
            return None

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

    # ── Bootstrap：兼容旧 AUTH_TOKEN 单 token 部署 ─────────
    def bootstrap_legacy_admin(self, legacy_token: str) -> None:
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

    # ── Role CRUD（新）─────────────────────────────────────
    def is_role_valid(self, role_key: str) -> bool:
        """role_key 是否对应 roles 表里某一行。"""
        if not role_key:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM roles WHERE role_key = ? LIMIT 1",
                (role_key,),
            ).fetchone()
        return row is not None

    def list_roles(self) -> list[tuple[Role, list[str]]]:
        """
        返回 [(Role, perm_keys)]，按 system desc + created_at asc 排序
        （内置角色排前面）。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM roles ORDER BY system DESC, created_at ASC"
            ).fetchall()
            perm_rows = conn.execute(
                "SELECT role_id, perm_key FROM role_permissions"
            ).fetchall()
        perms_by_role: dict[str, list[str]] = {}
        for r in perm_rows:
            perms_by_role.setdefault(r["role_id"], []).append(r["perm_key"])
        return [(self._row_to_role(r), perms_by_role.get(r["role_id"], [])) for r in rows]

    def get_role_by_key(self, role_key: str) -> Optional[tuple[Role, list[str]]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM roles WHERE role_key = ?", (role_key,)
            ).fetchone()
            if row is None:
                return None
            perm_rows = conn.execute(
                "SELECT perm_key FROM role_permissions WHERE role_id = ?",
                (row["role_id"],),
            ).fetchall()
        return self._row_to_role(row), [r["perm_key"] for r in perm_rows]

    def create_role(
        self,
        role_key: str,
        display_name: str,
        description: str = "",
        permissions: Iterable[str] = (),
    ) -> Role:
        role_key = (role_key or "").strip()
        display_name = (display_name or "").strip()
        if not role_key:
            raise ValueError("role_key 不能为空")
        if not display_name:
            raise ValueError("display_name 不能为空")
        # role_key 只能 ASCII 字母数字下划线（给代码引用 + 走 URL，避免编码问题）
        if not all((c.isascii() and c.isalnum()) or c == "_" for c in role_key):
            raise ValueError("role_key 只能包含英文字母、数字、下划线")

        perm_list = self._validate_permissions(permissions)

        role_id = f"role_{uuid.uuid4().hex[:12]}"
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO roles (role_id, role_key, display_name, description, system, "
                    "                    created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 0, ?, ?)",
                    (role_id, role_key, display_name, description or "", now, now),
                )
                for p in perm_list:
                    conn.execute(
                        "INSERT INTO role_permissions (role_id, perm_key) VALUES (?, ?)",
                        (role_id, p),
                    )
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as e:
                conn.execute("ROLLBACK")
                raise RoleKeyTakenError(f"role_key '{role_key}' 已存在") from e

        return Role(
            role_id=role_id,
            role_key=role_key,
            display_name=display_name,
            description=description or "",
            system=False,
            created_at=now,
            updated_at=now,
        )

    def update_role(
        self,
        role_key: str,
        *,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        permissions: Optional[Iterable[str]] = None,
    ) -> Role:
        """
        更新角色：display_name / description / permissions 三者可独立更新（任一 None 即不改）。

        admin 角色保护：
          - 可改 display_name / description（没风险）
          - permissions 禁止变更（硬保护：防止撤销 roles.manage 后全系统锁死）
        """
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM roles WHERE role_key = ?", (role_key,)
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    raise RoleNotFoundError(f"role_key '{role_key}' 不存在")

                # admin 保护
                if row["role_key"] == BUILTIN_ROLE_ADMIN and permissions is not None:
                    conn.execute("ROLLBACK")
                    raise ProtectedRoleError(
                        "admin 角色的权限受保护，不可修改（防锁死）"
                    )

                fields = []
                params = []
                if display_name is not None and display_name.strip():
                    fields.append("display_name = ?")
                    params.append(display_name.strip())
                if description is not None:
                    fields.append("description = ?")
                    params.append(description)
                if fields:
                    fields.append("updated_at = ?")
                    params.append(self._now())
                    params.append(row["role_id"])
                    conn.execute(
                        f"UPDATE roles SET {', '.join(fields)} WHERE role_id = ?",
                        params,
                    )

                if permissions is not None:
                    perm_list = self._validate_permissions(permissions)
                    conn.execute(
                        "DELETE FROM role_permissions WHERE role_id = ?",
                        (row["role_id"],),
                    )
                    for p in perm_list:
                        conn.execute(
                            "INSERT INTO role_permissions (role_id, perm_key) VALUES (?, ?)",
                            (row["role_id"], p),
                        )

                conn.execute("COMMIT")
            except (RoleNotFoundError, ProtectedRoleError, InvalidPermissionError):
                # 已 ROLLBACK，重抛
                raise
            except Exception:
                conn.execute("ROLLBACK")
                raise

        # 权限改动影响该角色所有用户；清空缓存最简单有效
        self._perm_cache.invalidate_all()

        updated, _ = self.get_role_by_key(role_key)
        assert updated is not None
        return updated

    def delete_role(self, role_key: str) -> None:
        """
        删除自定义角色。
        保护：
          - 内置角色不可删
          - 尚有用户使用该角色时不可删（避免孤儿用户）
        """
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM roles WHERE role_key = ?", (role_key,)
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    raise RoleNotFoundError(f"role_key '{role_key}' 不存在")
                if row["system"]:
                    conn.execute("ROLLBACK")
                    raise ProtectedRoleError(
                        f"role '{role_key}' 是系统内置角色，不可删除"
                    )

                # 检查是否还有活跃用户
                user_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM users WHERE role = ? AND active = 1",
                    (role_key,),
                ).fetchone()["c"]
                if user_count > 0:
                    conn.execute("ROLLBACK")
                    raise UserStoreError(
                        f"role '{role_key}' 还有 {user_count} 个活跃用户使用，"
                        f"请先将用户改到其他角色或停用"
                    )

                # role_permissions 通过 ON DELETE CASCADE 自动清理
                conn.execute("DELETE FROM roles WHERE role_id = ?", (row["role_id"],))
                conn.execute("COMMIT")
            except (RoleNotFoundError, ProtectedRoleError, UserStoreError):
                raise
            except Exception:
                conn.execute("ROLLBACK")
                raise

        self._perm_cache.invalidate_all()

    # ── 用户权限查询（中间件热路径）────────────────────────
    def get_user_permissions(self, user_id: str) -> frozenset[str]:
        """
        返回 user 当前的权限集合（空 frozenset 表示无权限 / 用户不存在 / 已停用）。

        走 _perm_cache，TTL 60s；roles / role_permissions 改动会主动失效缓存。
        """
        cached = self._perm_cache.get(user_id)
        if cached is not None:
            return cached

        with self._connect() as conn:
            row = conn.execute(
                "SELECT u.role, u.active FROM users u WHERE u.user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None or not row["active"]:
                self._perm_cache.set(user_id, frozenset())
                return frozenset()

            perm_rows = conn.execute(
                "SELECT rp.perm_key FROM role_permissions rp "
                "JOIN roles r ON rp.role_id = r.role_id "
                "WHERE r.role_key = ?",
                (row["role"],),
            ).fetchall()

        perms = frozenset(r["perm_key"] for r in perm_rows)
        self._perm_cache.set(user_id, perms)
        return perms

    def invalidate_permission_cache(self, user_id: Optional[str] = None) -> None:
        """公开接口：外部（比如 legacy admin 合成用户）也能触发失效。"""
        if user_id:
            self._perm_cache.invalidate_user(user_id)
        else:
            self._perm_cache.invalidate_all()

    # ── 内部 helpers ─────────────────────────────────────
    def _validate_permissions(self, perms: Iterable[str]) -> list[str]:
        """把传入的 perm_key 去重、排序、校验，非法 perm 立刻抛异常。"""
        # 去空白 + 去重 + 保持稳定顺序
        cleaned: list[str] = []
        seen: set[str] = set()
        for p in perms:
            p = (p or "").strip()
            if not p or p in seen:
                continue
            if not is_valid_perm_key(p):
                raise InvalidPermissionError(
                    f"perm_key '{p}' 未注册；可选: {sorted(ALL_PERM_KEYS)}"
                )
            seen.add(p)
            cleaned.append(p)
        return sorted(cleaned)

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

    @staticmethod
    def _row_to_role(row: sqlite3.Row) -> Role:
        return Role(
            role_id=row["role_id"],
            role_key=row["role_key"],
            display_name=row["display_name"],
            description=row["description"] or "",
            system=bool(row["system"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"] or "",
        )
