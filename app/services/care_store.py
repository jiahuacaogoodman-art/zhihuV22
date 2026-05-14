# -*- coding: utf-8 -*-
"""
@File    : app/services/care_store.py
@Desc    : 护理业务数据持久化层 (SQLite WAL)

覆盖模块：
  - 床位管理 (beds)
  - 护理等级 (care_levels + care_level_assignments)
  - 交接班 (handovers)
  - 异常事件上报 (incidents)
  - 护理记录留痕 (care_records)

设计决策：
  - 单独 SQLite 数据库文件 local_care/care.db，与 users.db / audit.db 分离
  - WAL 模式支持多线程并发读写
  - 统一使用 threading.Lock 保证进程内线程安全
  - 所有时间字段用 ISO 格式字符串（与项目其他模块保持一致）
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


class CareStore:
    """护理业务数据统一存储层。"""

    _CREATE_SQL = """
        -- 床位管理
        CREATE TABLE IF NOT EXISTS beds (
            bed_id      TEXT PRIMARY KEY,
            bed_number  TEXT NOT NULL UNIQUE,
            floor       TEXT NOT NULL DEFAULT '',
            building    TEXT NOT NULL DEFAULT '',
            room        TEXT NOT NULL DEFAULT '',
            bed_type    TEXT NOT NULL DEFAULT 'standard',
            status      TEXT NOT NULL DEFAULT 'available',
            patient_id  TEXT NOT NULL DEFAULT '',
            patient_name TEXT NOT NULL DEFAULT '',
            assigned_at TEXT NOT NULL DEFAULT '',
            notes       TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_beds_number ON beds(bed_number);
        CREATE INDEX IF NOT EXISTS idx_beds_status ON beds(status);
        CREATE INDEX IF NOT EXISTS idx_beds_patient ON beds(patient_id);

        -- 护理等级
        CREATE TABLE IF NOT EXISTS care_levels (
            level_id        TEXT PRIMARY KEY,
            level_key       TEXT NOT NULL UNIQUE,
            level_name      TEXT NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            daily_fee       REAL,
            service_items   TEXT NOT NULL DEFAULT '',
            min_nurse_ratio TEXT NOT NULL DEFAULT '',
            sort_order      INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_care_levels_key ON care_levels(level_key);

        -- 护理等级分配历史
        CREATE TABLE IF NOT EXISTS care_level_assignments (
            assignment_id TEXT PRIMARY KEY,
            patient_id    TEXT NOT NULL,
            level_key     TEXT NOT NULL,
            reason        TEXT NOT NULL DEFAULT '',
            assessed_by   TEXT NOT NULL DEFAULT '',
            assigned_at   TEXT NOT NULL,
            FOREIGN KEY (level_key) REFERENCES care_levels(level_key)
        );
        CREATE INDEX IF NOT EXISTS idx_cla_patient ON care_level_assignments(patient_id);

        -- 交接班
        CREATE TABLE IF NOT EXISTS handovers (
            handover_id     TEXT PRIMARY KEY,
            shift_from      TEXT NOT NULL,
            shift_to        TEXT NOT NULL,
            shift_type      TEXT NOT NULL DEFAULT 'day_to_night',
            patient_id      TEXT NOT NULL DEFAULT '',
            patient_name    TEXT NOT NULL DEFAULT '',
            situation       TEXT NOT NULL,
            background      TEXT NOT NULL,
            assessment      TEXT NOT NULL,
            recommendation  TEXT NOT NULL,
            pending_tasks   TEXT NOT NULL DEFAULT '',
            notes           TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'pending',
            acknowledged_at TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_handovers_patient ON handovers(patient_id);
        CREATE INDEX IF NOT EXISTS idx_handovers_status ON handovers(status);
        CREATE INDEX IF NOT EXISTS idx_handovers_time ON handovers(created_at);

        -- 异常事件
        CREATE TABLE IF NOT EXISTS incidents (
            incident_id      TEXT PRIMARY KEY,
            patient_id       TEXT NOT NULL,
            patient_name     TEXT NOT NULL DEFAULT '',
            incident_type    TEXT NOT NULL,
            severity         TEXT NOT NULL DEFAULT 'minor',
            status           TEXT NOT NULL DEFAULT 'reported',
            description      TEXT NOT NULL,
            location         TEXT NOT NULL DEFAULT '',
            occurred_at      TEXT NOT NULL DEFAULT '',
            reporter         TEXT NOT NULL DEFAULT '',
            witnesses        TEXT NOT NULL DEFAULT '',
            immediate_action TEXT NOT NULL DEFAULT '',
            follow_up        TEXT NOT NULL DEFAULT '',
            root_cause       TEXT NOT NULL DEFAULT '',
            prevention       TEXT NOT NULL DEFAULT '',
            resolved_by      TEXT NOT NULL DEFAULT '',
            resolved_at      TEXT NOT NULL DEFAULT '',
            notes            TEXT NOT NULL DEFAULT '',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_incidents_patient ON incidents(patient_id);
        CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);
        CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
        CREATE INDEX IF NOT EXISTS idx_incidents_time ON incidents(created_at);

        -- 护理记录
        CREATE TABLE IF NOT EXISTS care_records (
            record_id        TEXT PRIMARY KEY,
            patient_id       TEXT NOT NULL,
            patient_name     TEXT NOT NULL DEFAULT '',
            record_type      TEXT NOT NULL DEFAULT 'observation',
            content          TEXT NOT NULL,
            vital_data       TEXT NOT NULL DEFAULT '',
            recorded_by      TEXT NOT NULL DEFAULT '',
            recorded_at      TEXT NOT NULL,
            shift            TEXT NOT NULL DEFAULT '',
            related_event_id TEXT NOT NULL DEFAULT '',
            notes            TEXT NOT NULL DEFAULT '',
            created_at       TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_care_records_patient ON care_records(patient_id);
        CREATE INDEX IF NOT EXISTS idx_care_records_type ON care_records(record_type);
        CREATE INDEX IF NOT EXISTS idx_care_records_time ON care_records(recorded_at);
    """

    def __init__(self, db_path: str | Path):
        self._path = str(db_path)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(self._CREATE_SQL)
        logger.debug(f"CareStore 初始化完成: {self._path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _gen_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    # ================================================================
    # 床位管理
    # ================================================================
    def create_bed(self, data: dict) -> dict:
        bed_id = self._gen_id("bed")
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO beds (bed_id, bed_number, floor, building, room, bed_type, "
                    "status, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (bed_id, data["bed_number"], data.get("floor") or "",
                     data.get("building") or "", data.get("room") or "",
                     data.get("bed_type") or "standard", "available",
                     data.get("notes") or "", now, now),
                )
                conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                raise ValueError(f"床位编号 '{data['bed_number']}' 已存在")
        return self.get_bed(bed_id)

    def get_bed(self, bed_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM beds WHERE bed_id = ?", (bed_id,)).fetchone()
        return dict(row) if row else None

    def get_bed_by_number(self, bed_number: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM beds WHERE bed_number = ?", (bed_number,)).fetchone()
        return dict(row) if row else None

    def list_beds(self, status: Optional[str] = None, building: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM beds WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if building:
            sql += " AND building = ?"
            params.append(building)
        sql += " ORDER BY building, floor, room, bed_number"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def update_bed(self, bed_id: str, data: dict) -> Optional[dict]:
        fields = []
        params = []
        for key in ("bed_number", "floor", "building", "room", "bed_type", "status", "notes"):
            if key in data and data[key] is not None:
                fields.append(f"{key} = ?")
                params.append(data[key])
        if not fields:
            return self.get_bed(bed_id)
        fields.append("updated_at = ?")
        params.append(self._now())
        params.append(bed_id)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(f"UPDATE beds SET {', '.join(fields)} WHERE bed_id = ?", params)
                if cur.rowcount == 0:
                    conn.execute("ROLLBACK")
                    return None
                conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                raise ValueError(f"床位编号 '{data.get('bed_number')}' 已存在")
        return self.get_bed(bed_id)

    def assign_bed(self, bed_id: str, patient_id: str, patient_name: str = "") -> Optional[dict]:
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # 先释放该患者之前占用的床位
            conn.execute(
                "UPDATE beds SET status='available', patient_id='', patient_name='', assigned_at='', updated_at=? "
                "WHERE patient_id = ?",
                (now, patient_id),
            )
            cur = conn.execute(
                "UPDATE beds SET status='occupied', patient_id=?, patient_name=?, assigned_at=?, updated_at=? "
                "WHERE bed_id = ? AND status IN ('available', 'reserved')",
                (patient_id, patient_name, now, now, bed_id),
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return None
            conn.execute("COMMIT")
        return self.get_bed(bed_id)

    def release_bed(self, bed_id: str) -> Optional[dict]:
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE beds SET status='available', patient_id='', patient_name='', assigned_at='', updated_at=? "
                "WHERE bed_id = ?",
                (now, bed_id),
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return None
            conn.execute("COMMIT")
        return self.get_bed(bed_id)

    def delete_bed(self, bed_id: str) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("DELETE FROM beds WHERE bed_id = ? AND status != 'occupied'", (bed_id,))
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return False
            conn.execute("COMMIT")
        return True

    # ================================================================
    # 护理等级
    # ================================================================
    def create_care_level(self, data: dict) -> dict:
        level_id = self._gen_id("lvl")
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO care_levels (level_id, level_key, level_name, description, "
                    "daily_fee, service_items, min_nurse_ratio, sort_order, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (level_id, data["level_key"], data["level_name"],
                     data.get("description") or "", data.get("daily_fee"),
                     data.get("service_items") or "", data.get("min_nurse_ratio") or "",
                     data.get("sort_order") or 0, now, now),
                )
                conn.execute("COMMIT")
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                raise ValueError(f"护理等级 '{data['level_key']}' 已存在")
        return self.get_care_level(level_id)

    def get_care_level(self, level_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM care_levels WHERE level_id = ?", (level_id,)).fetchone()
        return dict(row) if row else None

    def get_care_level_by_key(self, level_key: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM care_levels WHERE level_key = ?", (level_key,)).fetchone()
        return dict(row) if row else None

    def list_care_levels(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM care_levels ORDER BY sort_order, level_key").fetchall()
            result = []
            for r in rows:
                d = dict(r)
                count = conn.execute(
                    "SELECT COUNT(*) as c FROM care_level_assignments WHERE level_key = ? "
                    "AND assignment_id IN (SELECT assignment_id FROM care_level_assignments "
                    "GROUP BY patient_id HAVING assigned_at = MAX(assigned_at))",
                    (d["level_key"],)
                ).fetchone()
                d["resident_count"] = count["c"] if count else 0
                result.append(d)
        return result

    def update_care_level(self, level_key: str, data: dict) -> Optional[dict]:
        fields = []
        params = []
        for key in ("level_name", "description", "daily_fee", "service_items", "min_nurse_ratio", "sort_order"):
            if key in data and data[key] is not None:
                fields.append(f"{key} = ?")
                params.append(data[key])
        if not fields:
            return self.get_care_level_by_key(level_key)
        fields.append("updated_at = ?")
        params.append(self._now())
        params.append(level_key)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(f"UPDATE care_levels SET {', '.join(fields)} WHERE level_key = ?", params)
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return None
            conn.execute("COMMIT")
        return self.get_care_level_by_key(level_key)

    def delete_care_level(self, level_key: str) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("DELETE FROM care_levels WHERE level_key = ?", (level_key,))
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return False
            conn.execute("COMMIT")
        return True

    def assign_care_level(self, patient_id: str, level_key: str, reason: str = "", assessed_by: str = "") -> dict:
        # Verify level exists
        level = self.get_care_level_by_key(level_key)
        if not level:
            raise ValueError(f"护理等级 '{level_key}' 不存在")
        assignment_id = self._gen_id("cla")
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO care_level_assignments (assignment_id, patient_id, level_key, reason, assessed_by, assigned_at) "
                "VALUES (?,?,?,?,?,?)",
                (assignment_id, patient_id, level_key, reason, assessed_by, now),
            )
            conn.execute("COMMIT")
        return {"assignment_id": assignment_id, "patient_id": patient_id,
                "level_key": level_key, "level_name": level["level_name"],
                "reason": reason, "assessed_by": assessed_by, "assigned_at": now}

    def get_patient_care_level(self, patient_id: str) -> Optional[dict]:
        """获取患者当前护理等级(最新一条分配记录)"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT a.*, l.level_name, l.daily_fee FROM care_level_assignments a "
                "JOIN care_levels l ON a.level_key = l.level_key "
                "WHERE a.patient_id = ? ORDER BY a.assigned_at DESC LIMIT 1",
                (patient_id,)
            ).fetchone()
        return dict(row) if row else None

    # ================================================================
    # 交接班
    # ================================================================
    def create_handover(self, data: dict) -> dict:
        handover_id = self._gen_id("hov")
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO handovers (handover_id, shift_from, shift_to, shift_type, "
                "patient_id, patient_name, situation, background, assessment, recommendation, "
                "pending_tasks, notes, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (handover_id, data["shift_from"], data["shift_to"],
                 data.get("shift_type") or "day_to_night",
                 data.get("patient_id") or "", data.get("patient_name") or "",
                 data["situation"], data["background"],
                 data["assessment"], data["recommendation"],
                 data.get("pending_tasks") or "", data.get("notes") or "",
                 "pending", now),
            )
            conn.execute("COMMIT")
        return self.get_handover(handover_id)

    def get_handover(self, handover_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM handovers WHERE handover_id = ?", (handover_id,)).fetchone()
        return dict(row) if row else None

    def list_handovers(self, patient_id: Optional[str] = None, status: Optional[str] = None,
                       limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM handovers WHERE 1=1"
        params: list = []
        if patient_id:
            sql += " AND patient_id = ?"
            params.append(patient_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def acknowledge_handover(self, handover_id: str, acknowledged_by: str = "", note: str = "") -> Optional[dict]:
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "UPDATE handovers SET status='acknowledged', acknowledged_at=?, notes=CASE WHEN notes='' THEN ? ELSE notes||'; '||? END "
                "WHERE handover_id = ? AND status='pending'",
                (now, note, note, handover_id),
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return None
            conn.execute("COMMIT")
        return self.get_handover(handover_id)

    # ================================================================
    # 异常事件上报
    # ================================================================
    def create_incident(self, data: dict) -> dict:
        incident_id = self._gen_id("inc")
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO incidents (incident_id, patient_id, patient_name, incident_type, "
                "severity, status, description, location, occurred_at, reporter, witnesses, "
                "immediate_action, notes, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (incident_id, data["patient_id"], data.get("patient_name") or "",
                 data["incident_type"], data.get("severity") or "minor", "reported",
                 data["description"], data.get("location") or "",
                 data.get("occurred_at") or now, data.get("reporter") or "",
                 data.get("witnesses") or "", data.get("immediate_action") or "",
                 data.get("notes") or "", now, now),
            )
            conn.execute("COMMIT")
        return self.get_incident(incident_id)

    def get_incident(self, incident_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
        return dict(row) if row else None

    def list_incidents(self, patient_id: Optional[str] = None, severity: Optional[str] = None,
                       status: Optional[str] = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM incidents WHERE 1=1"
        params: list = []
        if patient_id:
            sql += " AND patient_id = ?"
            params.append(patient_id)
        if severity:
            sql += " AND severity = ?"
            params.append(severity)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def update_incident(self, incident_id: str, data: dict) -> Optional[dict]:
        fields = []
        params = []
        for key in ("severity", "status", "description", "follow_up", "root_cause",
                    "prevention", "resolved_by", "resolved_at", "notes"):
            if key in data and data[key] is not None:
                fields.append(f"{key} = ?")
                params.append(data[key])
        if not fields:
            return self.get_incident(incident_id)
        fields.append("updated_at = ?")
        params.append(self._now())
        params.append(incident_id)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(f"UPDATE incidents SET {', '.join(fields)} WHERE incident_id = ?", params)
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                return None
            conn.execute("COMMIT")
        return self.get_incident(incident_id)

    def get_incident_stats(self, days: int = 30) -> dict:
        """异常事件统计"""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM incidents WHERE created_at >= ?", (cutoff,)
            ).fetchone()["c"]
            by_severity = {}
            for row in conn.execute(
                "SELECT severity, COUNT(*) as c FROM incidents WHERE created_at >= ? GROUP BY severity", (cutoff,)
            ).fetchall():
                by_severity[row["severity"]] = row["c"]
            by_type = {}
            for row in conn.execute(
                "SELECT incident_type, COUNT(*) as c FROM incidents WHERE created_at >= ? GROUP BY incident_type", (cutoff,)
            ).fetchall():
                by_type[row["incident_type"]] = row["c"]
            by_status = {}
            for row in conn.execute(
                "SELECT status, COUNT(*) as c FROM incidents WHERE created_at >= ? GROUP BY status", (cutoff,)
            ).fetchall():
                by_status[row["status"]] = row["c"]
        return {"total": total, "by_severity": by_severity,
                "by_type": by_type, "by_status": by_status, "period": f"近{days}天"}

    # ================================================================
    # 护理记录留痕
    # ================================================================
    def create_care_record(self, data: dict) -> dict:
        record_id = self._gen_id("rec")
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO care_records (record_id, patient_id, patient_name, record_type, "
                "content, vital_data, recorded_by, recorded_at, shift, related_event_id, notes, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (record_id, data["patient_id"], data.get("patient_name") or "",
                 data.get("record_type") or "observation", data["content"],
                 data.get("vital_data") or "", data.get("recorded_by") or "",
                 data.get("recorded_at") or now, data.get("shift") or "",
                 data.get("related_event_id") or "", data.get("notes") or "", now),
            )
            conn.execute("COMMIT")
        return self.get_care_record(record_id)

    def get_care_record(self, record_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM care_records WHERE record_id = ?", (record_id,)).fetchone()
        return dict(row) if row else None

    def list_care_records(self, patient_id: Optional[str] = None, record_type: Optional[str] = None,
                          shift: Optional[str] = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM care_records WHERE 1=1"
        params: list = []
        if patient_id:
            sql += " AND patient_id = ?"
            params.append(patient_id)
        if record_type:
            sql += " AND record_type = ?"
            params.append(record_type)
        if shift:
            sql += " AND shift = ?"
            params.append(shift)
        sql += " ORDER BY recorded_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


# ── 全局 singleton 工厂 ────────────────────────────────────
_care_store_singleton: Optional[CareStore] = None
_care_store_lock = threading.Lock()


def get_care_store(db_path: str | Path | None = None) -> CareStore:
    """获取全局 CareStore 实例（线程安全惰性单例）。"""
    global _care_store_singleton
    if _care_store_singleton is not None:
        return _care_store_singleton
    with _care_store_lock:
        if _care_store_singleton is not None:
            return _care_store_singleton
        if db_path is None:
            from app.core.config import BASE_DIR
            care_dir = Path(BASE_DIR) / "local_care"
            care_dir.mkdir(parents=True, exist_ok=True)
            db_path = care_dir / "care.db"
        _care_store_singleton = CareStore(db_path)
    return _care_store_singleton


def reset_care_store() -> None:
    """仅测试用"""
    global _care_store_singleton
    with _care_store_lock:
        _care_store_singleton = None
