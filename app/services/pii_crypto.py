# -*- coding: utf-8 -*-
"""
@File    : app/services/pii_crypto.py
@Desc    : 高敏感 PII 字段透明加密层（Fernet 对称加密）

保护的字段
  id_card             身份证号
  emergency_phone     紧急联系人电话
  emergency_contact   紧急联系人姓名
  emergency_relation  与老人的关系（父女、夫妻等）

工作原理
  - 写入 ChromaDB 之前：加密明文 → 密文字符串（base64 安全字符，可存 metadata）
  - 从 ChromaDB 读出之后：密文字符串 → 明文（透明解密）
  - 上层代码（ehr.py 路由/schema）完全不需要改动，只需在
    _build_metadata() 之前调用 encrypt_pii_fields()，
    在 _meta_to_record() 之后调用 decrypt_pii_fields()。

密钥配置
  环境变量 PII_ENCRYPTION_KEY：Fernet URL-safe base64 密钥（44 字符）。
  留空 → 加密关闭（开发 / 测试模式）。
  生成新密钥：
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

安全说明
  - Fernet 是 AES-128-CBC + HMAC-SHA256，对每条明文生成随机 IV，
    相同明文不会产生相同密文，防止基于密文的枚举攻击。
  - 密文带时间戳签名，可选设置 TTL（本实现不限制 TTL）。
  - 密钥必须通过环境变量注入，绝对不能硬编码在代码或 .env 里。
  - 旧数据（加密前写入）在首次加密部署后仍可正常读取（解密失败时
    自动降级返回原始值，并记录 warning）。
"""

from __future__ import annotations

import os
from loguru import logger

# Fernet 是可选依赖（cryptography 包）。如果未安装则关闭加密但不崩溃。
try:
    from cryptography.fernet import Fernet, InvalidToken
    _FERNET_AVAILABLE = True
except ImportError:
    _FERNET_AVAILABLE = False
    logger.warning("cryptography 包未安装，PII 字段加密已关闭。生产环境请 pip install cryptography")

# 需要加密的 metadata 字段名
PII_FIELDS: tuple[str, ...] = (
    "id_card",
    "emergency_phone",
    "emergency_contact",
    "emergency_relation",
)

# 加密标记前缀，用于区分"已加密的密文"与"明文原始值"（兼容旧数据）
_ENC_PREFIX = "enc:"


def _get_cipher() -> "Fernet | None":
    """懒加载：只在第一次调用时读取环境变量并构建 Fernet 实例。"""
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


def encrypt_pii_fields(meta: dict) -> dict:
    """
    将 meta 字典中的 PII 字段值加密，返回新字典（不修改原始对象）。
    如果密钥未配置或 cryptography 未安装，原样返回。
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
            continue  # 已经是密文，跳过
        try:
            ciphertext = cipher.encrypt(val.encode("utf-8")).decode("ascii")
            result[field] = _ENC_PREFIX + ciphertext
        except Exception as e:
            logger.error(f"字段 {field} 加密失败（保留明文）: {e}")
    return result


def decrypt_pii_fields(meta: dict) -> dict:
    """
    将 meta 字典中的 PII 字段密文解密，返回新字典（不修改原始对象）。
    如果某字段不是密文格式（旧数据），原样返回该字段（兼容旧数据）。
    """
    cipher = _get_cipher()
    if cipher is None:
        # 加密关闭：如果有前缀说明是之前加密环境写入的数据，无法解密，保留密文并警告
        result = dict(meta)
        for field in PII_FIELDS:
            val = result.get(field)
            if val and isinstance(val, str) and val.startswith(_ENC_PREFIX):
                logger.warning(
                    f"字段 {field} 是加密密文但 PII_ENCRYPTION_KEY 未配置，返回占位符"
                )
                result[field] = "[加密数据-需配置 PII_ENCRYPTION_KEY 才能读取]"
        return result

    result = dict(meta)
    for field in PII_FIELDS:
        val = result.get(field)
        if not val or not isinstance(val, str):
            continue
        if not val.startswith(_ENC_PREFIX):
            continue  # 明文旧数据，兼容返回
        ciphertext = val[len(_ENC_PREFIX):]
        try:
            plaintext = cipher.decrypt(ciphertext.encode("ascii")).decode("utf-8")
            result[field] = plaintext
        except Exception as e:
            logger.warning(f"字段 {field} 解密失败（可能密钥轮换后未迁移）: {e}")
            result[field] = "[解密失败]"
    return result
