# -*- coding: utf-8 -*-
"""
@File    : app/services/event_store.py
@Desc    : 护理事件持久化层（SQLite 替代原来的 events.json）

为什么换掉 events.json
  原实现是「先全量读取 → 修改 Python 列表 → 全量 JSON 序列化写回」。
  多护工并发时两个进程会各自读到旧版本，最后一个写入的把另一个覆盖
  ——即"Last Write Wins"，数据静默丢失。

新实现
  - 存储：SQLite WAL 模式，单文件，同目录下自动创建。
  - 并发：每个进程用 threading.Lock 序列化写操作；
           SQLite WAL 同时允许多个只读并发。
  - 格式：每条事件在 SQLite 里以 JSON 字符串存储（TEXT 列），
           接口仍然暴露 Python dict，调用方感知不到底层变化。
  - 迁移：首次启动时自动把旧 events.json 里的数据导入 SQLite，
           然后把 events.json 重命名为 .bak（不删，留作备份）。

公开接口（与原 _load_events / _save_events 等保持语义一致）
  load_events(patient_id=None, status_filter=None) -> list[dict]
  save_event(event: dict) -> None          # 插入或更新（按 event_id）
  get_event(event_id: str) -> dict | None
  update_event(event_id: str, updater: Callable) -> dict   # 原子读改写
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Callable

from loguru import logger


class EventStore:
    """SQLite 驱动的护理事件存储，进程内线程安全。"""

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS nursing_events (
            event_id    TEXT PRIMARY KEY,
            patient_id  TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'processing',
            created_at  TEXT NOT NULL DEFAULT '',
            data        TEXT NOT NULL        -- 完整事件 JSON
        );
        CREATE INDEX IF NOT EXISTS idx_patient ON nursing_events(patient_id);
        CREATE INDEX IF NOT EXISTS idx_status  ON nursing_events(status);
    """

    def __init__(self, db_path: str | Path, legacy_json: str | Path | None = None):
        self._path = str(db_path)
        self._lock = threading.Lock()
        self._init_db()
        if legacy_json:
            self._migrate_from_json(Path(legacy_json))

    # ── 初始化 ──────────────────────────────────────────────
    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(self._CREATE_TABLE)
        logger.debug(f"EventStore 初始化完成: {self._path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            isolation_level=None,   # autocommit；事务由我们手动管理
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    # ── 旧数据迁移 ───────────────────────────────────────────
    def _migrate_from_json(self, json_path: Path) -> None:
        if not json_path.exists():
            return
        # 如果 SQLite 里已有数据，说明之前已迁移过，跳过
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM nursing_events").fetchone()[0]
            if count > 0:
                logger.debug("EventStore: SQLite 已有数据，跳过 JSON 迁移")
                return
        try:
            events = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(events, list):
                return
            for ev in events:
                if isinstance(ev, dict) and ev.get("event_id"):
                    self.save_event(ev)
            bak = json_path.with_suffix(".json.bak")
            json_path.rename(bak)
            logger.success(f"EventStore: 已将 {len(events)} 条事件从 JSON 迁移至 SQLite，原文件已重命名为 {bak.name}")
        except Exception as e:
            logger.warning(f"EventStore: JSON 迁移失败（不影响新写入）: {e}")

    # ── 读 ──────────────────────────────────────────────────
    def load_events(
        self,
        patient_id: str | None = None,
        status_filter: str | None = None,
    ) -> list[dict]:
        sql = "SELECT data FROM nursing_events WHERE 1=1"
        params: list = []
        if patient_id:
            sql += " AND patient_id = ?"
            params.append(patient_id)
        if status_filter:
            sql += " AND status = ?"
            params.append(status_filter)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def get_event(self, event_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT data FROM nursing_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    # ── 写 ──────────────────────────────────────────────────
    def save_event(self, event: dict) -> None:
        """插入或覆盖更新（UPSERT）。"""
        event_id = event.get("event_id") or ""
        patient_id = event.get("patient_id") or ""
        status = event.get("status") or "processing"
        created_at = event.get("created_at") or ""
        data = json.dumps(event, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO nursing_events (event_id, patient_id, status, created_at, data)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    patient_id = excluded.patient_id,
                    status     = excluded.status,
                    created_at = excluded.created_at,
                    data       = excluded.data
                """,
                (event_id, patient_id, status, created_at, data),
            )
            conn.execute("COMMIT")

    def update_event(self, event_id: str, updater: Callable[[dict], dict]) -> dict:
        """原子读-改-写：在锁内完成，updater 抛异常则回滚。"""
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data FROM nursing_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise KeyError(event_id)
            event = json.loads(row["data"])
            updated = updater(event)          # 业务逻辑在锁内执行
            new_data = json.dumps(updated, ensure_ascii=False)
            conn.execute(
                """
                UPDATE nursing_events
                SET data = ?, status = ?, patient_id = ?
                WHERE event_id = ?
                """,
                (new_data, updated.get("status", "processing"),
                 updated.get("patient_id", ""), event_id),
            )
            conn.execute("COMMIT")
        return updated
