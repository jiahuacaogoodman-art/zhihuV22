# -*- coding: utf-8 -*-
"""
@File    : app/services/audit_log.py
@Desc    : 操作审计日志（Audit Trail）

职责
  记录所有对患者档案的写操作（增 / 改 / 删），以及病历照片的上传与删除。
  每条审计记录包含：时间戳、操作类型、操作者标识、patient_id、
  doc_id（如适用）、操作摘要，以及变更前后的字段差异（diff）。

存储
  SQLite WAL 模式，单独数据库文件 local_audit_log/audit.db，
  与业务数据库分离，防止审计记录被业务操作意外覆盖。

公开接口
  log(action, patient_id, operator, *, doc_id=None, detail=None, diff=None)
  query(patient_id=None, action=None, operator=None, limit=100) -> list[dict]

操作类型（action 枚举字符串）
  PATIENT_CREATE  新建患者基本档案
  PATIENT_UPDATE  修改患者基本档案
  PATIENT_DELETE  删除患者全部档案
  RECORD_UPLOAD   上传病历照片
  RECORD_DELETE   删除病历照片档案
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


class AuditLog:
    """线程安全的审计日志，WAL 模式 SQLite。"""

    _CREATE_SQL = """
        CREATE TABLE IF NOT EXISTS audit_log (
            id         TEXT    PRIMARY KEY,
            ts         TEXT    NOT NULL,
            action     TEXT    NOT NULL,
            patient_id TEXT    NOT NULL DEFAULT '',
            operator   TEXT    NOT NULL DEFAULT 'unknown',
            doc_id     TEXT    NOT NULL DEFAULT '',
            detail     TEXT    NOT NULL DEFAULT '',
            diff       TEXT    NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_audit_patient ON audit_log(patient_id);
        CREATE INDEX IF NOT EXISTS idx_audit_action  ON audit_log(action);
        CREATE INDEX IF NOT EXISTS idx_audit_ts      ON audit_log(ts);
    """

    def __init__(self, db_path: str | Path):
        self._path = str(db_path)
        self._lock = threading.Lock()
        self._init()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(self._CREATE_SQL)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            isolation_level=None,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    # ── 写 ──────────────────────────────────────────────────
    def log(
        self,
        action: str,
        patient_id: str,
        operator: str,
        *,
        doc_id: str = "",
        detail: str = "",
        diff: dict[str, Any] | None = None,
    ) -> None:
        """
        记录一条审计事件。

        diff 格式示例（UPDATE 操作）：
          {"before": {"name": "张三", "bed_number": "A1"},
           "after":  {"name": "张三", "bed_number": "B2"}}
        """
        entry_id = f"aud_{uuid.uuid4().hex[:12]}"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        diff_str = json.dumps(diff or {}, ensure_ascii=False)
        try:
            with self._lock, self._conn() as c:
                c.execute("BEGIN IMMEDIATE")
                c.execute(
                    "INSERT INTO audit_log (id,ts,action,patient_id,operator,doc_id,detail,diff) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (entry_id, ts, action, patient_id, operator, doc_id, detail, diff_str),
                )
                c.execute("COMMIT")
        except Exception as e:
            # 审计写入失败绝不能阻断主业务流程，仅记录 warning
            logger.warning(f"审计日志写入失败 (不影响业务): {e}")

    # ── 读 ──────────────────────────────────────────────────
    def query(
        self,
        patient_id: str | None = None,
        action: str | None = None,
        operator: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: list = []
        if patient_id:
            sql += " AND patient_id = ?"
            params.append(patient_id)
        if action:
            sql += " AND action = ?"
            params.append(action)
        if operator:
            sql += " AND operator = ?"
            params.append(operator)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        with self._conn() as c:
            rows = c.execute(sql, params).fetchall()

        result = []
        for row in rows:
            entry = dict(row)
            try:
                entry["diff"] = json.loads(entry["diff"])
            except Exception:
                pass
            result.append(entry)
        return result


def _diff_meta(before: dict, after: dict, fields: list[str]) -> dict:
    """
    比较关心的字段，返回 {"before": {...}, "after": {...}} 格式的变更摘要。

    Phase 1B 语义契约（调用方必须遵守）:
      · `before` 和 `after` 都必须是**明文** dict（调用方在传入前对读自 ChromaDB 的
        密文做 decrypt_pii_fields）。
      · 本函数内对 PII 字段做 mask，**不输出** PII 明文，更不会输出密文。
      · 非 PII 字段（床位号、年龄、病史片段）原样进入 diff，保留审计可读性。

    为什么要把 decrypt 留给调用方：
      审计日志是"有无变化"的权威来源。如果在本函数里做 decrypt，decrypt 失败
      会让 diff 误判为"有变化"，污染审计。调用方自己掌控解密时机后，可以
      选择把解密失败的字段排除在 diff 之外。

    输出示例（PII 字段变化时不泄露具体值）:
      {"before": {"bed_number": "A1", "name": "[已加密]"},
       "after":  {"bed_number": "B2", "name": "[已加密]"}}
    """
    # 延迟导入，避免 services/audit_log 硬依赖 services/pii_crypto 的模块加载时序
    from app.services.pii_crypto import PII_FIELDS

    pii_set = set(PII_FIELDS)
    b: dict[str, Any] = {}
    a: dict[str, Any] = {}
    for f in fields:
        bv = before.get(f)
        av = after.get(f)
        if bv == av:
            continue
        if f in pii_set:
            # PII 字段：仅表达"有变化"，不写具体值（无论明文密文都 mask）
            b[f] = "[已加密]"
            a[f] = "[已加密]"
        else:
            b[f] = bv
            a[f] = av
    return {"before": b, "after": a} if b or a else {}
