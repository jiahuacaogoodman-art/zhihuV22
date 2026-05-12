# -*- coding: utf-8 -*-
"""
@File    : app/services/billing_store.py
@Desc    : 账务持久化：入住（admissions）/ 账单（bills）/ 收款（payments）

职责
  - 以机构最小需求为界：一位老人一次入住 = 一个 admission；
    每月按入住关系开一张账单；一张账单可以有多笔收款（分期 / 混合支付）。
  - 所有金额以"分"存储（INTEGER），避免浮点误差；对外接口统一用"元"（float）。
  - 状态机只在本模块维护：unpaid / partial / paid / void。

存储
  独立 SQLite 文件 local_billing/billing.db（WAL）。
  与 users.db / audit.db 分离，互不牵连。

不做什么
  - 不做开票发票（税务 / 发票抬头不在 P1 范围）
  - 不做自动按月批量生成（P1 手动出账；批量可在 P2 UI 上加"一键月结"）
  - 不做退款到支付通道（只做作废 = 红冲账单余额）

关键 edge case
  - 作废一笔收款（void_payment）：该笔金额从 bill.amount_paid 减回，重算 status
  - 删入住（delete_admission）：仅当无任何账单存在时允许
  - 同月重复出账：(admission_id, billing_month) UNIQUE，重复抛 BillExistsError
  - 收款金额超出账单剩余：接口层校验 + DB CHECK 兜底（防并发超收）
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

from loguru import logger


# ── 异常类型 ────────────────────────────────────────────
class BillingError(Exception):
    """账务领域基类异常。"""


class AdmissionNotFoundError(BillingError):
    pass


class AdmissionHasBillsError(BillingError):
    """入住记录已有账单，禁止删除。"""


class BillNotFoundError(BillingError):
    pass


class BillExistsError(BillingError):
    """同一入住同一账期已有账单。"""


class BillVoidedError(BillingError):
    """账单已被作废，不能再收款 / 不能再作废。"""


class PaymentNotFoundError(BillingError):
    pass


class PaymentAlreadyVoidedError(BillingError):
    pass


class PaymentExceedsRemainingError(BillingError):
    """本次收款额超过账单剩余可收金额。"""


# ── 不可变数据对象 ──────────────────────────────────────
# 对外金额以元计，内部存分以避免浮点误差。
# 构造时始终传元，to_dict() 统一返回元。

@dataclass(frozen=True)
class Admission:
    admission_id: str
    patient_id: str
    patient_name: str
    bed_number: str
    care_level: str
    monthly_fee: float                 # 元
    admission_date: str                # YYYY-MM-DD
    discharge_date: str                # YYYY-MM-DD，空串表示在住
    note: str
    created_at: str
    updated_at: str

    @property
    def is_active(self) -> bool:
        return not self.discharge_date

    def to_dict(self) -> dict:
        return {
            "admission_id": self.admission_id,
            "patient_id": self.patient_id,
            "patient_name": self.patient_name,
            "bed_number": self.bed_number,
            "care_level": self.care_level,
            "monthly_fee": self.monthly_fee,
            "admission_date": self.admission_date,
            "discharge_date": self.discharge_date,
            "is_active": self.is_active,
            "note": self.note,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class Bill:
    bill_id: str
    admission_id: str
    patient_id: str
    patient_name: str
    billing_month: str                 # YYYY-MM
    bed_fee: float                     # 元
    care_fee: float
    other_fee: float
    amount_due: float                  # 累计应收 = bed + care + other
    amount_paid: float                 # 已收（不含已作废）
    amount_remaining: float            # amount_due - amount_paid
    status: str                        # unpaid / partial / paid / void
    note: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "bill_id": self.bill_id,
            "admission_id": self.admission_id,
            "patient_id": self.patient_id,
            "patient_name": self.patient_name,
            "billing_month": self.billing_month,
            "bed_fee": self.bed_fee,
            "care_fee": self.care_fee,
            "other_fee": self.other_fee,
            "amount_due": self.amount_due,
            "amount_paid": self.amount_paid,
            "amount_remaining": self.amount_remaining,
            "status": self.status,
            "note": self.note,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class Payment:
    payment_id: str
    bill_id: str
    amount: float                      # 元
    method: str                        # cash / wechat / alipay / bank / other
    paid_at: str                       # YYYY-MM-DD HH:MM:SS
    received_by: str                   # 操作人 username
    note: str
    voided_at: str                     # 空串=有效
    voided_by: str
    void_reason: str
    created_at: str

    @property
    def is_voided(self) -> bool:
        return bool(self.voided_at)

    def to_dict(self) -> dict:
        return {
            "payment_id": self.payment_id,
            "bill_id": self.bill_id,
            "amount": self.amount,
            "method": self.method,
            "paid_at": self.paid_at,
            "received_by": self.received_by,
            "note": self.note,
            "is_voided": self.is_voided,
            "voided_at": self.voided_at,
            "voided_by": self.voided_by,
            "void_reason": self.void_reason,
            "created_at": self.created_at,
        }


# ── 工具：元 ↔ 分 ───────────────────────────────────────
def _yuan_to_fen(yuan) -> int:
    """元 → 分。接收 float / int / str；小数部分四舍五入到分。"""
    if yuan is None:
        return 0
    # Decimal 从 str 最精确；从 float 走 repr 以保留 2 位
    d = Decimal(str(yuan))
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _fen_to_yuan(fen: int) -> float:
    return round((fen or 0) / 100.0, 2)


# ── 合法值集合 ──────────────────────────────────────────
VALID_PAYMENT_METHODS = frozenset({"cash", "wechat", "alipay", "bank", "other"})
VALID_BILL_STATUSES = frozenset({"unpaid", "partial", "paid", "void"})


def _validate_month(billing_month: str) -> None:
    """YYYY-MM 格式校验。"""
    try:
        datetime.strptime(billing_month, "%Y-%m")
    except Exception as e:
        raise BillingError(f"billing_month 必须是 YYYY-MM 格式：{billing_month}") from e


def _validate_date(date_str: str, field: str) -> None:
    if not date_str:
        return
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except Exception as e:
        raise BillingError(f"{field} 必须是 YYYY-MM-DD 格式：{date_str}") from e


# ── 主存储类 ────────────────────────────────────────────
class BillingStore:
    """线程安全的账务存储（独立 SQLite，WAL）。"""

    _CREATE_SQL = """
        CREATE TABLE IF NOT EXISTS admissions (
            admission_id    TEXT PRIMARY KEY,
            patient_id      TEXT NOT NULL,
            patient_name    TEXT NOT NULL DEFAULT '',
            bed_number      TEXT NOT NULL DEFAULT '',
            care_level      TEXT NOT NULL DEFAULT '',
            monthly_fee_fen INTEGER NOT NULL DEFAULT 0 CHECK (monthly_fee_fen >= 0),
            admission_date  TEXT NOT NULL,
            discharge_date  TEXT NOT NULL DEFAULT '',
            note            TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_admissions_patient ON admissions(patient_id);
        CREATE INDEX IF NOT EXISTS idx_admissions_active  ON admissions(discharge_date);

        CREATE TABLE IF NOT EXISTS bills (
            bill_id            TEXT PRIMARY KEY,
            admission_id       TEXT NOT NULL,
            billing_month      TEXT NOT NULL,
            bed_fee_fen        INTEGER NOT NULL DEFAULT 0 CHECK (bed_fee_fen >= 0),
            care_fee_fen       INTEGER NOT NULL DEFAULT 0 CHECK (care_fee_fen >= 0),
            other_fee_fen      INTEGER NOT NULL DEFAULT 0 CHECK (other_fee_fen >= 0),
            amount_due_fen     INTEGER NOT NULL CHECK (amount_due_fen >= 0),
            amount_paid_fen    INTEGER NOT NULL DEFAULT 0 CHECK (amount_paid_fen >= 0),
            status             TEXT NOT NULL DEFAULT 'unpaid'
                               CHECK (status IN ('unpaid','partial','paid','void')),
            note               TEXT NOT NULL DEFAULT '',
            created_at         TEXT NOT NULL,
            updated_at         TEXT NOT NULL,
            UNIQUE (admission_id, billing_month),
            -- amount_paid 必须 ≤ amount_due（防并发超收）
            CHECK (amount_paid_fen <= amount_due_fen),
            FOREIGN KEY (admission_id) REFERENCES admissions(admission_id)
        );
        CREATE INDEX IF NOT EXISTS idx_bills_admission ON bills(admission_id);
        CREATE INDEX IF NOT EXISTS idx_bills_month     ON bills(billing_month);
        CREATE INDEX IF NOT EXISTS idx_bills_status    ON bills(status);

        CREATE TABLE IF NOT EXISTS payments (
            payment_id   TEXT PRIMARY KEY,
            bill_id      TEXT NOT NULL,
            amount_fen   INTEGER NOT NULL CHECK (amount_fen > 0),
            method       TEXT NOT NULL DEFAULT 'cash',
            paid_at      TEXT NOT NULL,
            received_by  TEXT NOT NULL DEFAULT '',
            note         TEXT NOT NULL DEFAULT '',
            voided_at    TEXT NOT NULL DEFAULT '',
            voided_by    TEXT NOT NULL DEFAULT '',
            void_reason  TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL,
            FOREIGN KEY (bill_id) REFERENCES bills(bill_id)
        );
        CREATE INDEX IF NOT EXISTS idx_payments_bill  ON payments(bill_id);
        CREATE INDEX IF NOT EXISTS idx_payments_void  ON payments(voided_at);
    """

    def __init__(self, db_path: str | Path):
        self._path = str(db_path)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(self._CREATE_SQL)
        logger.debug(f"BillingStore 初始化完成: {self._path}")

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

    # ── Admission CRUD ─────────────────────────────────
    def create_admission(
        self,
        patient_id: str,
        patient_name: str,
        admission_date: str,
        *,
        bed_number: str = "",
        care_level: str = "",
        monthly_fee: float = 0.0,
        note: str = "",
    ) -> Admission:
        patient_id = (patient_id or "").strip()
        if not patient_id:
            raise ValueError("patient_id 不能为空")
        patient_name = (patient_name or "").strip()
        if not patient_name:
            raise ValueError("patient_name 不能为空")
        _validate_date(admission_date, "admission_date")
        if not admission_date:
            raise ValueError("admission_date 不能为空")
        monthly_fee_fen = _yuan_to_fen(monthly_fee)
        if monthly_fee_fen < 0:
            raise ValueError("monthly_fee 不能为负")

        admission_id = f"adm_{uuid.uuid4().hex[:12]}"
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO admissions (admission_id, patient_id, patient_name, "
                    "    bed_number, care_level, monthly_fee_fen, admission_date, "
                    "    discharge_date, note, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)",
                    (admission_id, patient_id, patient_name, bed_number or "",
                     care_level or "", monthly_fee_fen, admission_date,
                     note or "", now, now),
                )
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as e:
                conn.execute("ROLLBACK")
                raise BillingError(f"创建入住失败: {e}") from e

        return Admission(
            admission_id=admission_id,
            patient_id=patient_id,
            patient_name=patient_name,
            bed_number=bed_number or "",
            care_level=care_level or "",
            monthly_fee=_fen_to_yuan(monthly_fee_fen),
            admission_date=admission_date,
            discharge_date="",
            note=note or "",
            created_at=now,
            updated_at=now,
        )

    def get_admission(self, admission_id: str) -> Optional[Admission]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM admissions WHERE admission_id = ?",
                (admission_id,),
            ).fetchone()
        return self._row_to_admission(row) if row else None

    def list_admissions(
        self,
        patient_id: Optional[str] = None,
        include_discharged: bool = True,
    ) -> list[Admission]:
        sql = "SELECT * FROM admissions WHERE 1 = 1"
        params: list = []
        if patient_id:
            sql += " AND patient_id = ?"
            params.append(patient_id)
        if not include_discharged:
            sql += " AND discharge_date = ''"
        sql += " ORDER BY admission_date DESC, created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_admission(r) for r in rows]

    def update_admission(
        self,
        admission_id: str,
        *,
        patient_name: Optional[str] = None,
        bed_number: Optional[str] = None,
        care_level: Optional[str] = None,
        monthly_fee: Optional[float] = None,
        admission_date: Optional[str] = None,
        discharge_date: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Admission:
        """任何字段为 None 表示不改；显式传 "" 可清空 discharge_date 等字段。"""
        if admission_date is not None:
            _validate_date(admission_date, "admission_date")
        if discharge_date is not None:
            _validate_date(discharge_date, "discharge_date")

        fields: list[str] = []
        params: list = []
        if patient_name is not None:
            fields.append("patient_name = ?"); params.append(patient_name)
        if bed_number is not None:
            fields.append("bed_number = ?"); params.append(bed_number)
        if care_level is not None:
            fields.append("care_level = ?"); params.append(care_level)
        if monthly_fee is not None:
            fen = _yuan_to_fen(monthly_fee)
            if fen < 0:
                raise ValueError("monthly_fee 不能为负")
            fields.append("monthly_fee_fen = ?"); params.append(fen)
        if admission_date is not None:
            if not admission_date:
                raise ValueError("admission_date 不能置空")
            fields.append("admission_date = ?"); params.append(admission_date)
        if discharge_date is not None:
            fields.append("discharge_date = ?"); params.append(discharge_date)
        if note is not None:
            fields.append("note = ?"); params.append(note)

        if not fields:
            adm = self.get_admission(admission_id)
            if adm is None:
                raise AdmissionNotFoundError(f"admission_id '{admission_id}' 不存在")
            return adm

        fields.append("updated_at = ?")
        params.append(self._now())
        params.append(admission_id)

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                f"UPDATE admissions SET {', '.join(fields)} WHERE admission_id = ?",
                params,
            )
            if cur.rowcount == 0:
                conn.execute("ROLLBACK")
                raise AdmissionNotFoundError(f"admission_id '{admission_id}' 不存在")
            conn.execute("COMMIT")

        updated = self.get_admission(admission_id)
        assert updated is not None
        return updated

    def discharge(self, admission_id: str, discharge_date: str) -> Admission:
        """办理出住：写 discharge_date。"""
        _validate_date(discharge_date, "discharge_date")
        if not discharge_date:
            raise ValueError("discharge_date 不能为空")
        return self.update_admission(admission_id, discharge_date=discharge_date)

    def delete_admission(self, admission_id: str) -> None:
        """硬删。仅当未产生任何账单时允许，否则抛 AdmissionHasBillsError。"""
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT 1 FROM admissions WHERE admission_id = ?", (admission_id,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    raise AdmissionNotFoundError(
                        f"admission_id '{admission_id}' 不存在")
                cnt = conn.execute(
                    "SELECT COUNT(*) AS c FROM bills WHERE admission_id = ?",
                    (admission_id,),
                ).fetchone()["c"]
                if cnt > 0:
                    conn.execute("ROLLBACK")
                    raise AdmissionHasBillsError(
                        f"该入住已关联 {cnt} 张账单，不能删除；请改为办理出住")
                conn.execute(
                    "DELETE FROM admissions WHERE admission_id = ?",
                    (admission_id,),
                )
                conn.execute("COMMIT")
            except (AdmissionNotFoundError, AdmissionHasBillsError):
                raise
            except Exception:
                conn.execute("ROLLBACK")
                raise

    # ── Bill CRUD ──────────────────────────────────────
    def create_bill(
        self,
        admission_id: str,
        billing_month: str,
        *,
        bed_fee: float = 0.0,
        care_fee: float = 0.0,
        other_fee: float = 0.0,
        note: str = "",
    ) -> Bill:
        _validate_month(billing_month)
        bed = _yuan_to_fen(bed_fee)
        care = _yuan_to_fen(care_fee)
        other = _yuan_to_fen(other_fee)
        if min(bed, care, other) < 0:
            raise ValueError("各项费用不能为负")
        due = bed + care + other
        if due <= 0:
            raise ValueError("账单总额必须大于 0")

        bill_id = f"bil_{uuid.uuid4().hex[:12]}"
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                adm_row = conn.execute(
                    "SELECT 1 FROM admissions WHERE admission_id = ?",
                    (admission_id,),
                ).fetchone()
                if adm_row is None:
                    conn.execute("ROLLBACK")
                    raise AdmissionNotFoundError(
                        f"admission_id '{admission_id}' 不存在")
                conn.execute(
                    "INSERT INTO bills (bill_id, admission_id, billing_month, "
                    "    bed_fee_fen, care_fee_fen, other_fee_fen, "
                    "    amount_due_fen, amount_paid_fen, status, note, "
                    "    created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'unpaid', ?, ?, ?)",
                    (bill_id, admission_id, billing_month,
                     bed, care, other, due, note or "", now, now),
                )
                conn.execute("COMMIT")
            except sqlite3.IntegrityError as e:
                conn.execute("ROLLBACK")
                # UNIQUE 冲突 = 同月重复出账
                if "UNIQUE" in str(e).upper():
                    raise BillExistsError(
                        f"账单已存在：admission={admission_id}, month={billing_month}"
                    ) from e
                raise BillingError(f"创建账单失败: {e}") from e
            except (AdmissionNotFoundError, BillExistsError):
                raise

        return self.get_bill(bill_id)  # type: ignore[return-value]

    def get_bill(self, bill_id: str) -> Optional[Bill]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT b.*, a.patient_id AS _pid, a.patient_name AS _pname "
                "FROM bills b JOIN admissions a ON b.admission_id = a.admission_id "
                "WHERE b.bill_id = ?",
                (bill_id,),
            ).fetchone()
        return self._row_to_bill(row) if row else None

    def list_bills(
        self,
        admission_id: Optional[str] = None,
        patient_id: Optional[str] = None,
        billing_month: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[Bill]:
        sql = (
            "SELECT b.*, a.patient_id AS _pid, a.patient_name AS _pname "
            "FROM bills b JOIN admissions a ON b.admission_id = a.admission_id "
            "WHERE 1 = 1"
        )
        params: list = []
        if admission_id:
            sql += " AND b.admission_id = ?"; params.append(admission_id)
        if patient_id:
            sql += " AND a.patient_id = ?"; params.append(patient_id)
        if billing_month:
            _validate_month(billing_month)
            sql += " AND b.billing_month = ?"; params.append(billing_month)
        if status:
            if status not in VALID_BILL_STATUSES:
                raise ValueError(f"status 必须是 {sorted(VALID_BILL_STATUSES)} 之一")
            sql += " AND b.status = ?"; params.append(status)
        sql += " ORDER BY b.billing_month DESC, b.created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_bill(r) for r in rows]

    def void_bill(self, bill_id: str, reason: str = "") -> Bill:
        """
        作废账单：仅当未收到任何钱（amount_paid_fen == 0）时允许。
        （如果已经有收款，应先把每笔 payment 一一作废再作废账单，
        保证金额账目可审计、不残留悬念。）
        """
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT status, amount_paid_fen, note FROM bills WHERE bill_id = ?",
                    (bill_id,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    raise BillNotFoundError(f"bill_id '{bill_id}' 不存在")
                if row["status"] == "void":
                    conn.execute("ROLLBACK")
                    raise BillVoidedError("账单已作废")
                if row["amount_paid_fen"] > 0:
                    conn.execute("ROLLBACK")
                    raise BillingError(
                        "账单已有有效收款，无法直接作废；请先逐笔作废收款后再作废账单"
                    )
                new_note = row["note"]
                if reason:
                    prefix = f"[作废] {reason}"
                    new_note = f"{prefix}\n{new_note}" if new_note else prefix
                conn.execute(
                    "UPDATE bills SET status = 'void', note = ?, updated_at = ? "
                    "WHERE bill_id = ?",
                    (new_note, now, bill_id),
                )
                conn.execute("COMMIT")
            except (BillNotFoundError, BillVoidedError, BillingError):
                raise
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get_bill(bill_id)  # type: ignore[return-value]

    # ── Payment CRUD ───────────────────────────────────
    def add_payment(
        self,
        bill_id: str,
        amount: float,
        *,
        method: str = "cash",
        received_by: str = "",
        note: str = "",
        paid_at: Optional[str] = None,
    ) -> Payment:
        """
        新增一笔收款。
          · 原子：同一事务里更新 bills.amount_paid_fen + status
          · 超收防护：amount > remaining 直接抛 PaymentExceedsRemainingError
          · 账单已作废 → BillVoidedError
        """
        method = (method or "cash").lower()
        if method not in VALID_PAYMENT_METHODS:
            raise ValueError(
                f"method 必须是 {sorted(VALID_PAYMENT_METHODS)} 之一"
            )
        amt_fen = _yuan_to_fen(amount)
        if amt_fen <= 0:
            raise ValueError("收款金额必须大于 0")

        now = self._now()
        paid_at_val = paid_at or now
        payment_id = f"pay_{uuid.uuid4().hex[:12]}"

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                bill = conn.execute(
                    "SELECT status, amount_due_fen, amount_paid_fen "
                    "FROM bills WHERE bill_id = ?",
                    (bill_id,),
                ).fetchone()
                if bill is None:
                    conn.execute("ROLLBACK")
                    raise BillNotFoundError(f"bill_id '{bill_id}' 不存在")
                if bill["status"] == "void":
                    conn.execute("ROLLBACK")
                    raise BillVoidedError("账单已作废，不能再登记收款")
                remaining = bill["amount_due_fen"] - bill["amount_paid_fen"]
                if amt_fen > remaining:
                    conn.execute("ROLLBACK")
                    raise PaymentExceedsRemainingError(
                        f"收款金额（{_fen_to_yuan(amt_fen)}）超过"
                        f"账单剩余可收（{_fen_to_yuan(remaining)}）"
                    )

                new_paid = bill["amount_paid_fen"] + amt_fen
                new_status = self._compute_status(
                    bill["amount_due_fen"], new_paid
                )
                conn.execute(
                    "INSERT INTO payments (payment_id, bill_id, amount_fen, method, "
                    "    paid_at, received_by, note, voided_at, voided_by, "
                    "    void_reason, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, '', '', '', ?)",
                    (payment_id, bill_id, amt_fen, method,
                     paid_at_val, received_by or "", note or "", now),
                )
                conn.execute(
                    "UPDATE bills SET amount_paid_fen = ?, status = ?, updated_at = ? "
                    "WHERE bill_id = ?",
                    (new_paid, new_status, now, bill_id),
                )
                conn.execute("COMMIT")
            except (BillNotFoundError, BillVoidedError,
                    PaymentExceedsRemainingError):
                raise
            except Exception:
                conn.execute("ROLLBACK")
                raise

        p = self.get_payment(payment_id)
        assert p is not None
        return p

    def get_payment(self, payment_id: str) -> Optional[Payment]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payments WHERE payment_id = ?", (payment_id,),
            ).fetchone()
        return self._row_to_payment(row) if row else None

    def list_payments(
        self,
        bill_id: Optional[str] = None,
        include_voided: bool = True,
    ) -> list[Payment]:
        sql = "SELECT * FROM payments WHERE 1 = 1"
        params: list = []
        if bill_id:
            sql += " AND bill_id = ?"; params.append(bill_id)
        if not include_voided:
            sql += " AND voided_at = ''"
        sql += " ORDER BY paid_at DESC, created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_payment(r) for r in rows]

    def void_payment(
        self,
        payment_id: str,
        *,
        voided_by: str = "",
        reason: str = "",
    ) -> Payment:
        """
        软作废一笔收款，并**同步回滚** bill.amount_paid_fen 与 status。
        已作废的 payment 不能再次作废。账单已 void 状态允许作废收款
        （但这种组合在业务上不应出现——bill 作废前必须先把 payment 全作废）。
        """
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                p = conn.execute(
                    "SELECT bill_id, amount_fen, voided_at FROM payments "
                    "WHERE payment_id = ?",
                    (payment_id,),
                ).fetchone()
                if p is None:
                    conn.execute("ROLLBACK")
                    raise PaymentNotFoundError(
                        f"payment_id '{payment_id}' 不存在")
                if p["voided_at"]:
                    conn.execute("ROLLBACK")
                    raise PaymentAlreadyVoidedError("该笔收款已作废")

                bill = conn.execute(
                    "SELECT status, amount_due_fen, amount_paid_fen "
                    "FROM bills WHERE bill_id = ?",
                    (p["bill_id"],),
                ).fetchone()
                # bill 一定存在（FK 保证）
                new_paid = bill["amount_paid_fen"] - p["amount_fen"]
                if new_paid < 0:
                    # 理论不可能到这儿（CHECK 保证），兜底
                    conn.execute("ROLLBACK")
                    raise BillingError(
                        "数据异常：作废后 amount_paid_fen 将为负")

                # 如果 bill 当前是 void，保持 void（作废 payment 不改 bill 状态）
                if bill["status"] == "void":
                    new_status = "void"
                else:
                    new_status = self._compute_status(
                        bill["amount_due_fen"], new_paid)

                conn.execute(
                    "UPDATE payments SET voided_at = ?, voided_by = ?, "
                    "    void_reason = ? WHERE payment_id = ?",
                    (now, voided_by or "", reason or "", payment_id),
                )
                conn.execute(
                    "UPDATE bills SET amount_paid_fen = ?, status = ?, updated_at = ? "
                    "WHERE bill_id = ?",
                    (new_paid, new_status, now, p["bill_id"]),
                )
                conn.execute("COMMIT")
            except (PaymentNotFoundError, PaymentAlreadyVoidedError, BillingError):
                raise
            except Exception:
                conn.execute("ROLLBACK")
                raise

        out = self.get_payment(payment_id)
        assert out is not None
        return out

    # ── 汇总查询（UI 看板用）──────────────────────────
    def summary(
        self,
        billing_month: Optional[str] = None,
    ) -> dict:
        """
        UI 看板：返回应收 / 已收 / 未收、按状态分桶账单数。
        billing_month=None 表示全部。
        """
        sql = "SELECT status, COUNT(*) AS c, " \
              "       SUM(amount_due_fen)  AS due, " \
              "       SUM(amount_paid_fen) AS paid " \
              "FROM bills WHERE 1 = 1"
        params: list = []
        if billing_month:
            _validate_month(billing_month)
            sql += " AND billing_month = ?"; params.append(billing_month)
        sql += " GROUP BY status"

        buckets = {s: {"count": 0, "due": 0.0, "paid": 0.0}
                   for s in VALID_BILL_STATUSES}
        total_due_fen = 0
        total_paid_fen = 0
        with self._connect() as conn:
            for row in conn.execute(sql, params).fetchall():
                st = row["status"]
                due = row["due"] or 0
                paid = row["paid"] or 0
                buckets[st] = {
                    "count": row["c"],
                    "due": _fen_to_yuan(due),
                    "paid": _fen_to_yuan(paid),
                }
                # void 账单不计入总额（不是有效应收）
                if st != "void":
                    total_due_fen += due
                    total_paid_fen += paid
        return {
            "billing_month": billing_month or "",
            "total_due": _fen_to_yuan(total_due_fen),
            "total_paid": _fen_to_yuan(total_paid_fen),
            "total_remaining": _fen_to_yuan(total_due_fen - total_paid_fen),
            "by_status": buckets,
        }

    # ── 状态机 ────────────────────────────────────────
    @staticmethod
    def _compute_status(amount_due_fen: int, amount_paid_fen: int) -> str:
        """
        状态机（不含 void；void 由调用方显式置位）：
          amount_paid == 0            → unpaid
          0 < amount_paid < amount_due → partial
          amount_paid == amount_due   → paid
        """
        if amount_paid_fen <= 0:
            return "unpaid"
        if amount_paid_fen >= amount_due_fen:
            return "paid"
        return "partial"

    # ── Row → dataclass ───────────────────────────────
    @staticmethod
    def _row_to_admission(row: sqlite3.Row) -> Admission:
        return Admission(
            admission_id=row["admission_id"],
            patient_id=row["patient_id"],
            patient_name=row["patient_name"] or "",
            bed_number=row["bed_number"] or "",
            care_level=row["care_level"] or "",
            monthly_fee=_fen_to_yuan(row["monthly_fee_fen"]),
            admission_date=row["admission_date"],
            discharge_date=row["discharge_date"] or "",
            note=row["note"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_bill(row: sqlite3.Row) -> Bill:
        due = row["amount_due_fen"]
        paid = row["amount_paid_fen"]
        return Bill(
            bill_id=row["bill_id"],
            admission_id=row["admission_id"],
            patient_id=row["_pid"],
            patient_name=row["_pname"] or "",
            billing_month=row["billing_month"],
            bed_fee=_fen_to_yuan(row["bed_fee_fen"]),
            care_fee=_fen_to_yuan(row["care_fee_fen"]),
            other_fee=_fen_to_yuan(row["other_fee_fen"]),
            amount_due=_fen_to_yuan(due),
            amount_paid=_fen_to_yuan(paid),
            amount_remaining=_fen_to_yuan(due - paid),
            status=row["status"],
            note=row["note"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_payment(row: sqlite3.Row) -> Payment:
        return Payment(
            payment_id=row["payment_id"],
            bill_id=row["bill_id"],
            amount=_fen_to_yuan(row["amount_fen"]),
            method=row["method"],
            paid_at=row["paid_at"],
            received_by=row["received_by"] or "",
            note=row["note"] or "",
            voided_at=row["voided_at"] or "",
            voided_by=row["voided_by"] or "",
            void_reason=row["void_reason"] or "",
            created_at=row["created_at"],
        )


__all__ = [
    # dataclasses
    "Admission",
    "Bill",
    "Payment",
    # store
    "BillingStore",
    # exceptions
    "BillingError",
    "AdmissionNotFoundError",
    "AdmissionHasBillsError",
    "BillNotFoundError",
    "BillExistsError",
    "BillVoidedError",
    "PaymentNotFoundError",
    "PaymentAlreadyVoidedError",
    "PaymentExceedsRemainingError",
    # helpers
    "VALID_PAYMENT_METHODS",
    "VALID_BILL_STATUSES",
]
