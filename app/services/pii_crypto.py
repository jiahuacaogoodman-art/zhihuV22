# -*- coding: utf-8 -*-
"""
@File    : app/services/pii_crypto.py
@Desc    : 高敏感 PII 字段透明加密层（Fernet 对称加密）

Phase 1B 起保护的字段（10 个）
  身份证类       id_card
  联系人类       emergency_contact / emergency_phone / emergency_relation
  身份识别类     name / birth_date
  机构定位类     bed_number / primary_nurse
  病理敏感类     allergy
  自由文本       notes

不加密的字段（且不纳入 PII）
  medical_history  病史与用药正文，要参与向量化检索，无法对称加密
  age / gender / blood_type / height_cm / weight_kg / care_level
  admission_date / diet_restriction
  → 建议写入前由上层做脱敏/粗粒度化（如 age → 年龄段）

工作原理
  - 写入 ChromaDB 之前：encrypt_pii_fields(meta)   明文 → 密文
  - 从 ChromaDB 读出后：decrypt_pii_fields(meta)   密文 → 明文
  - 密文带前缀 "enc:"，便于识别；双重加密由前缀检查防止

密钥配置
  环境变量 PII_ENCRYPTION_KEY：Fernet URL-safe base64 密钥（44 字符）。
  留空 → 加密关闭（disabled），仅限开发/测试。
  生成：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

可观测性
  - is_encryption_enabled()  /health 会调用此方法，向运维暴露加密开关状态
  - 密钥未配置 + 已存在密文数据 → 解密返回占位符并记录 warning
  - 密钥无效 → 启动时 logger.error，加密降级为 disabled

安全说明
  - Fernet = AES-128-CBC + HMAC-SHA256，对每条明文用随机 IV；
    相同明文加密后密文不同，防止枚举攻击。
  - 密钥通过环境变量注入；切勿硬编码。
  - 密钥轮换：本 PR 暂不支持，将在后续 Phase 引入双密钥过渡窗。
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

from loguru import logger

# Fernet 是可选依赖（cryptography 包）。未安装时关闭加密但不崩溃。
try:
    from cryptography.fernet import Fernet, InvalidToken  # noqa: F401 (re-export for tests)
    _FERNET_AVAILABLE = True
except ImportError:
    _FERNET_AVAILABLE = False
    logger.warning(
        "cryptography 包未安装，PII 字段加密已关闭。生产环境请 pip install cryptography"
    )

# ── 需要加密的 metadata 字段名 ──────────────────────────────
# Phase 1B 扩展：从 4 个扩到 10 个，覆盖姓名 + 床位 + 主管护工 + 病理敏感
PII_FIELDS: tuple[str, ...] = (
    # 身份证 / 联系人（Phase 1 已覆盖）
    "id_card",
    "emergency_contact",
    "emergency_phone",
    "emergency_relation",
    # 身份识别（Phase 1B 新增）
    "name",
    "birth_date",
    # 机构内定位（Phase 1B 新增）
    "bed_number",
    "primary_nurse",
    # 病理/生活敏感（Phase 1B 新增）
    "allergy",
    "notes",
)

# 加密标记前缀，用于区分"已加密密文"与"明文/旧数据"，防止双重加密
_ENC_PREFIX = "enc:"

# 显示层占位符
_MASK_DISPLAY = "[已加密]"
_MASK_MISSING_KEY = "[加密数据-需配置 PII_ENCRYPTION_KEY 才能读取]"
_MASK_DECRYPT_FAILED = "[解密失败]"


def is_pii_field(field: str) -> bool:
    """外部（如 audit_log._diff_meta）判断某字段是否属于 PII。"""
    return field in PII_FIELDS


def _get_cipher() -> Optional["Fernet"]:
    """懒加载：每次调用重新读取环境变量（支持启动后 rotate key，但本 PR 不正式承诺）。"""
    key = os.getenv("PII_ENCRYPTION_KEY", "").strip()
    if not key:
        return None
    if not _FERNET_AVAILABLE:
        return None
    try:
        return Fernet(key.encode())
    except Exception as e:
        logger.error(f"PII_ENCRYPTION_KEY 无效，加密已关闭: {e}")
        return None


def is_encryption_enabled() -> bool:
    """
    向外暴露"加密当前是否生效"，供 /health、启动日志使用。

    生效条件：cryptography 已安装 + PII_ENCRYPTION_KEY 非空且合法。
    """
    return _get_cipher() is not None


def encrypt_pii_fields(meta: dict) -> dict:
    """
    将 meta 字典中的 PII 字段加密，返回新字典（不修改原对象）。
    密钥未配置或 cryptography 未安装 → 原样返回。
    已经是密文（带 enc: 前缀）→ 跳过，防止双重加密。
    """
    cipher = _get_cipher()
    if cipher is None:
        return meta
    result = dict(meta)
    for field in PII_FIELDS:
        val = result.get(field)
        if not val or not isinstance(val, str):
            continue
        if val.startswith(_ENC_PREFIX):
            continue  # 已是密文
        try:
            ciphertext = cipher.encrypt(val.encode("utf-8")).decode("ascii")
            result[field] = _ENC_PREFIX + ciphertext
        except Exception as e:
            logger.error(f"字段 {field} 加密失败（保留明文）: {e}")
    return result


def decrypt_pii_fields(meta: dict) -> dict:
    """
    将 meta 字典中的 PII 字段密文解密，返回新字典。

    密钥未配置 + 数据是密文 → 返回占位符（避免泄露原始密文到响应）。
    密钥配置但解密失败 → 返回 "[解密失败]"（可能密钥轮换未迁移）。
    字段非密文（旧数据）→ 原样返回（平滑升级）。
    """
    cipher = _get_cipher()
    if cipher is None:
        result = dict(meta)
        for field in PII_FIELDS:
            val = result.get(field)
            if val and isinstance(val, str) and val.startswith(_ENC_PREFIX):
                logger.warning(
                    f"字段 {field} 是加密密文但 PII_ENCRYPTION_KEY 未配置，返回占位符"
                )
                result[field] = _MASK_MISSING_KEY
        return result

    result = dict(meta)
    for field in PII_FIELDS:
        val = result.get(field)
        if not val or not isinstance(val, str):
            continue
        if not val.startswith(_ENC_PREFIX):
            continue  # 明文（旧数据或尚未加密）
        ciphertext = val[len(_ENC_PREFIX):]
        try:
            plaintext = cipher.decrypt(ciphertext.encode("ascii")).decode("utf-8")
            result[field] = plaintext
        except Exception as e:
            logger.warning(f"字段 {field} 解密失败（可能密钥轮换后未迁移）: {e}")
            result[field] = _MASK_DECRYPT_FAILED
    return result


def mask_pii_fields(meta: dict, fields: Optional[Iterable[str]] = None) -> dict:
    """
    把 meta 里的 PII 字段用占位符替换（不做加解密）。
    专用于"审计日志 diff"等展示场景：需要表达"有变化"但不能泄露具体值。

    Args:
      meta: 输入字典
      fields: 只处理这些字段；默认所有 PII_FIELDS

    Returns:
      新字典，PII 字段值换成 "[已加密]"，非 PII 字段保持原值
    """
    target = set(fields) if fields is not None else set(PII_FIELDS)
    result = dict(meta)
    for field in target:
        if field not in PII_FIELDS:
            continue
        if field in result and result[field] not in (None, ""):
            result[field] = _MASK_DISPLAY
    return result


def is_ciphertext(value) -> bool:
    """判断字符串是否是加密后的密文（外部审计、导出脱敏可能需要）。"""
    return isinstance(value, str) and value.startswith(_ENC_PREFIX)
