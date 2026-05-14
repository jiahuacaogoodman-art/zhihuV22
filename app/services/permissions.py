# -*- coding: utf-8 -*-
"""
@File    : app/services/permissions.py
@Desc    : 权限点 registry —— RBAC 的"事实源"

设计决策
  - Code-first：所有权限点在本文件里声明为常量，代码直接引用
    （`require_permission(PERM_USERS_MANAGE)`），防止 typo。
  - 启动时 sync：UserStore 初始化时把本清单同步进 permissions 表，
    新增权限点只改代码、删权限点要过 migration。
  - 内置角色 seed：同一个清单顺便定义三个内置角色默认拥有哪些权限点，
    首次启动无任何自定义角色时写入 roles 表。

新增权限点的步骤
  1. 在 ALL_PERMISSIONS 里加一行：(perm_key, category, display_name, description)
  2. 决定三个内置角色谁该默认有，写进 BUILTIN_ROLE_PERMISSIONS
  3. 在路由里用 require_permission("xxx.xxx") 守卫
  4. 跑测试、升级部署——启动时会自动 sync 进 DB

为什么不做权限层级 / 通配
  - 扁平点清单 + UI 勾选对小团队足够直观
  - 加通配（ehr.*）会让权限检查代码多一层展开，且前端 UI 难解释
  - 真要分层等 P2 再加
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


# ── 权限点常量 ────────────────────────────────────────────
# 命名约定：<category>.<action>
# category 与 UI 分组对应，action 动词优先（read / write / manage / ...）

# 认证与用户管理
PERM_USERS_MANAGE = "users.manage"          # 创建/停用用户
PERM_TOKENS_MANAGE = "tokens.manage"        # 签发/吊销 API Key
PERM_ROLES_MANAGE = "roles.manage"          # 管理自定义角色与权限

# 档案管理（EHR）
PERM_EHR_READ = "ehr.read"                  # 查看老人档案
PERM_EHR_WRITE = "ehr.write"                # 新增/修改/删除档案
PERM_EHR_AUDIT_READ = "ehr.audit_read"      # 查看操作审计日志

# 护理决策
PERM_NURSING_DECISION = "nursing.decision"  # 调用 AI 决策 / 提示词优化
PERM_NURSING_TASKCARD = "nursing.taskcard"  # 生成 / 更新任务卡 / 事件闭环

# 床位管理
PERM_BED_READ = "bed.read"                  # 查看床位
PERM_BED_WRITE = "bed.write"                # 管理床位（新增/修改/分配/释放）

# 护理等级
PERM_CARE_LEVEL_READ = "care_level.read"    # 查看护理等级
PERM_CARE_LEVEL_WRITE = "care_level.write"  # 管理护理等级

# 交接班
PERM_HANDOVER_READ = "handover.read"        # 查看交接班记录
PERM_HANDOVER_WRITE = "handover.write"      # 创建/确认交接班

# 异常事件
PERM_INCIDENT_READ = "incident.read"        # 查看异常事件
PERM_INCIDENT_WRITE = "incident.write"      # 上报/处理异常事件

# 护理记录
PERM_CARE_RECORD_READ = "care_record.read"  # 查看护理记录
PERM_CARE_RECORD_WRITE = "care_record.write"  # 创建护理记录


# ── 权限点元数据 ─────────────────────────────────────────
@dataclass(frozen=True)
class PermissionSpec:
    """权限点声明，用于 UI 渲染 + DB sync。"""

    perm_key: str
    category: str               # 分组（对应前端 UI 的 collapse 分节）
    display_name: str           # 中文名
    description: str            # 一句话说明用途

    def to_dict(self) -> dict:
        return {
            "perm_key": self.perm_key,
            "category": self.category,
            "display_name": self.display_name,
            "description": self.description,
        }


# 完整权限清单。**新增时改这里**。
ALL_PERMISSIONS: tuple[PermissionSpec, ...] = (
    # 认证与用户管理
    PermissionSpec(
        perm_key=PERM_USERS_MANAGE,
        category="auth",
        display_name="用户管理",
        description="创建、停用用户账户",
    ),
    PermissionSpec(
        perm_key=PERM_TOKENS_MANAGE,
        category="auth",
        display_name="API Key 管理",
        description="为用户签发或吊销 API Key（登录凭证）",
    ),
    PermissionSpec(
        perm_key=PERM_ROLES_MANAGE,
        category="auth",
        display_name="角色权限管理",
        description="管理自定义角色和权限分配（高风险）",
    ),
    # 档案管理
    PermissionSpec(
        perm_key=PERM_EHR_READ,
        category="ehr",
        display_name="查看老人档案",
        description="查询老人档案、病历照片、OCR 文本",
    ),
    PermissionSpec(
        perm_key=PERM_EHR_WRITE,
        category="ehr",
        display_name="修改老人档案",
        description="新增、编辑、删除档案及病历上传",
    ),
    PermissionSpec(
        perm_key=PERM_EHR_AUDIT_READ,
        category="ehr",
        display_name="查看审计日志",
        description="查看谁在什么时候对哪位老人做了什么操作",
    ),
    # 护理决策
    PermissionSpec(
        perm_key=PERM_NURSING_DECISION,
        category="nursing",
        display_name="AI 护理建议",
        description="调用 AI 获取护理建议、提示词优化、决策记忆",
    ),
    PermissionSpec(
        perm_key=PERM_NURSING_TASKCARD,
        category="nursing",
        display_name="护理任务卡",
        description="生成任务卡、打卡、记录观察、归档事件",
    ),
    # 床位管理
    PermissionSpec(
        perm_key=PERM_BED_READ,
        category="bed",
        display_name="查看床位",
        description="查询床位列表、状态、分配情况",
    ),
    PermissionSpec(
        perm_key=PERM_BED_WRITE,
        category="bed",
        display_name="管理床位",
        description="新增、修改、删除床位及分配/释放",
    ),
    # 护理等级
    PermissionSpec(
        perm_key=PERM_CARE_LEVEL_READ,
        category="care_level",
        display_name="查看护理等级",
        description="查询护理等级定义及老人等级分配",
    ),
    PermissionSpec(
        perm_key=PERM_CARE_LEVEL_WRITE,
        category="care_level",
        display_name="管理护理等级",
        description="定义等级、调整老人护理等级",
    ),
    # 交接班
    PermissionSpec(
        perm_key=PERM_HANDOVER_READ,
        category="handover",
        display_name="查看交接班",
        description="查询 SBAR 交接班记录",
    ),
    PermissionSpec(
        perm_key=PERM_HANDOVER_WRITE,
        category="handover",
        display_name="交接班操作",
        description="创建交接记录、确认接班",
    ),
    # 异常事件
    PermissionSpec(
        perm_key=PERM_INCIDENT_READ,
        category="incident",
        display_name="查看异常事件",
        description="查询异常事件列表及统计",
    ),
    PermissionSpec(
        perm_key=PERM_INCIDENT_WRITE,
        category="incident",
        display_name="上报/处理异常事件",
        description="上报异常事件、更新处理进度、关闭事件",
    ),
    # 护理记录
    PermissionSpec(
        perm_key=PERM_CARE_RECORD_READ,
        category="care_record",
        display_name="查看护理记录",
        description="查询护理操作记录、生命体征等留痕数据",
    ),
    PermissionSpec(
        perm_key=PERM_CARE_RECORD_WRITE,
        category="care_record",
        display_name="创建护理记录",
        description="记录护理操作、生命体征、饮食、用药等",
    ),
)

# 快查索引：perm_key → PermissionSpec
PERMISSION_INDEX: dict[str, PermissionSpec] = {p.perm_key: p for p in ALL_PERMISSIONS}

# 所有合法 perm_key 集合（供 DB 层校验 + 失效清理）
ALL_PERM_KEYS: frozenset[str] = frozenset(p.perm_key for p in ALL_PERMISSIONS)


# ── 内置角色默认权限 ─────────────────────────────────────
# 这三个内置角色在 UserStore._sync_permissions() 首次初始化时写入 roles 表，
# 之后可以在 UI 里改它们的权限（除了 admin 不能改、不能删以防锁死）。
#
# 设计：
#   - admin:      全部权限（硬实现保护，UI 也无法取消）
#   - nurse:      档案读写 + 护理决策 + 任务卡，不管用户、不看审计
#   - caregiver:  档案只读 + 护理决策 + 任务卡，不能写档案（Phase 0 默认是能写的，
#                 这里保持兼容：给 caregiver 也加 ehr.write，迁移后管理员可随时在 UI 改）

BUILTIN_ROLE_ADMIN = "admin"
BUILTIN_ROLE_NURSE = "nurse"
BUILTIN_ROLE_CAREGIVER = "caregiver"

# role_key -> display_name
BUILTIN_ROLES: dict[str, str] = {
    BUILTIN_ROLE_ADMIN: "系统管理员",
    BUILTIN_ROLE_NURSE: "护士",
    BUILTIN_ROLE_CAREGIVER: "护工",
}

# role_key -> default permission set
# admin 用 "*" 通配表示"自动授予所有注册权限点"，sync 时展开
BUILTIN_ROLE_PERMISSIONS: dict[str, Sequence[str]] = {
    BUILTIN_ROLE_ADMIN: ("*",),
    BUILTIN_ROLE_NURSE: (
        PERM_EHR_READ,
        PERM_EHR_WRITE,
        PERM_NURSING_DECISION,
        PERM_NURSING_TASKCARD,
        PERM_BED_READ,
        PERM_BED_WRITE,
        PERM_CARE_LEVEL_READ,
        PERM_CARE_LEVEL_WRITE,
        PERM_HANDOVER_READ,
        PERM_HANDOVER_WRITE,
        PERM_INCIDENT_READ,
        PERM_INCIDENT_WRITE,
        PERM_CARE_RECORD_READ,
        PERM_CARE_RECORD_WRITE,
    ),
    BUILTIN_ROLE_CAREGIVER: (
        PERM_EHR_READ,
        # 保留兼容：改造前 caregiver 能写档案，这里先保留，管理员事后可撤销
        PERM_EHR_WRITE,
        PERM_NURSING_DECISION,
        PERM_NURSING_TASKCARD,
        PERM_BED_READ,
        PERM_CARE_LEVEL_READ,
        PERM_HANDOVER_READ,
        PERM_HANDOVER_WRITE,
        PERM_INCIDENT_READ,
        PERM_INCIDENT_WRITE,
        PERM_CARE_RECORD_READ,
        PERM_CARE_RECORD_WRITE,
    ),
}


def expand_builtin_perms(role_key: str) -> set[str]:
    """把某内置角色的默认权限展开成具体 perm_key 集合（含 '*' 展开）。"""
    raw = BUILTIN_ROLE_PERMISSIONS.get(role_key, ())
    if "*" in raw:
        return set(ALL_PERM_KEYS)
    return set(raw)


def is_valid_perm_key(perm_key: str) -> bool:
    return perm_key in ALL_PERM_KEYS


def permissions_by_category() -> dict[str, list[dict]]:
    """UI 展示用：按 category 分组返回，保持 ALL_PERMISSIONS 声明顺序。"""
    grouped: dict[str, list[dict]] = {}
    for p in ALL_PERMISSIONS:
        grouped.setdefault(p.category, []).append(p.to_dict())
    return grouped


__all__ = [
    # perm keys
    "PERM_USERS_MANAGE",
    "PERM_TOKENS_MANAGE",
    "PERM_ROLES_MANAGE",
    "PERM_EHR_READ",
    "PERM_EHR_WRITE",
    "PERM_EHR_AUDIT_READ",
    "PERM_NURSING_DECISION",
    "PERM_NURSING_TASKCARD",
    "PERM_BED_READ",
    "PERM_BED_WRITE",
    "PERM_CARE_LEVEL_READ",
    "PERM_CARE_LEVEL_WRITE",
    "PERM_HANDOVER_READ",
    "PERM_HANDOVER_WRITE",
    "PERM_INCIDENT_READ",
    "PERM_INCIDENT_WRITE",
    "PERM_CARE_RECORD_READ",
    "PERM_CARE_RECORD_WRITE",
    # structures
    "PermissionSpec",
    "ALL_PERMISSIONS",
    "PERMISSION_INDEX",
    "ALL_PERM_KEYS",
    # builtin roles
    "BUILTIN_ROLE_ADMIN",
    "BUILTIN_ROLE_NURSE",
    "BUILTIN_ROLE_CAREGIVER",
    "BUILTIN_ROLES",
    "BUILTIN_ROLE_PERMISSIONS",
    # helpers
    "expand_builtin_perms",
    "is_valid_perm_key",
    "permissions_by_category",
]
