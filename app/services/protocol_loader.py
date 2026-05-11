# -*- coding: utf-8 -*-
"""
@File    : app/services/protocol_loader.py
@Desc    : 护理协议模板热加载器

功能
  - 从 data/protocols.yaml 读取协议定义（替代 nursing.py 里的硬编码 PROTOCOLS 字典）
  - 文件修改后自动重载（基于 mtime 检测，每次 get_protocols() 调用时检查）
  - 读取失败时回退到上一份合法数据，不崩溃服务

使用方式
  from app.services.protocol_loader import get_protocols

  protocols = get_protocols()   # dict[str, dict]
  # 等价于原来的 PROTOCOLS 常量，可直接替换
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from loguru import logger

# YAML 是可选依赖（PyYAML）。未安装时回退到空协议集并记录 warning。
try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False
    logger.warning("PyYAML 未安装，协议热加载已关闭。请 pip install pyyaml")

_DEFAULT_PROTOCOLS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "protocols.yaml"

_lock = threading.Lock()
_cache: dict[str, Any] = {}          # key → protocol dict
_cache_mtime: float = -1.0            # 上次成功加载时的文件 mtime


def _load_from_yaml(path: Path) -> dict[str, Any]:
    """
    从 YAML 文件加载并基础校验协议字典。
    校验规则：每条协议必须有 event_type 和 immediate_tasks。
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("protocols.yaml 顶层必须是 mapping（字典）")
    for key, val in raw.items():
        if not isinstance(val, dict):
            raise ValueError(f"协议 {key!r} 格式错误：必须是字典")
        if "event_type" not in val:
            raise ValueError(f"协议 {key!r} 缺少 event_type 字段")
        if "immediate_tasks" not in val:
            raise ValueError(f"协议 {key!r} 缺少 immediate_tasks 字段")
    return raw


def get_protocols(path: Path | None = None) -> dict[str, Any]:
    """
    获取当前有效的协议字典。

    每次调用时检查文件 mtime；仅在文件更新后才重新解析 YAML，
    保证热加载开销趋近于零（一次 stat() syscall）。
    """
    global _cache, _cache_mtime

    if not _YAML_AVAILABLE:
        return {}

    target = path or _DEFAULT_PROTOCOLS_PATH

    if not target.exists():
        logger.warning(f"协议文件不存在: {target}，返回空协议集")
        return {}

    try:
        mtime = target.stat().st_mtime
    except OSError:
        return dict(_cache)   # 出错时返回上次缓存

    with _lock:
        if mtime == _cache_mtime and _cache:
            return dict(_cache)   # 文件未变，直接返回缓存副本

        try:
            new_protocols = _load_from_yaml(target)
            _cache = new_protocols
            _cache_mtime = mtime
            logger.info(f"协议模板已（重）载: {len(new_protocols)} 条, 来源: {target}")
        except Exception as e:
            logger.error(f"协议文件解析失败，继续使用上次缓存: {e}")

        return dict(_cache)


def reload_protocols(path: Path | None = None) -> dict[str, Any]:
    """强制重载（用于运维手动触发或测试）。"""
    global _cache_mtime
    with _lock:
        _cache_mtime = -1.0
    return get_protocols(path)
