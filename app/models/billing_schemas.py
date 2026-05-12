# -*- coding: utf-8 -*-
"""
@File    : app/models/billing_schemas.py
@Desc    : Billing P1 请求 / 响应 Pydantic Schema
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ── 通用 ────────────────────────────────────────────────
class _CodeMessage(BaseModel):
    code: int = 200
    message: Optional[str] = None


# ── Admission ────────────────────────────────────────────
class AdmissionResponse(BaseModel):
    admission_id: str
    patient_id: str
    patient_name: str
    bed_number: str
    care_level: str
    monthly_fee: float
    admission_date: str
    discharge_date: str
    is_active: bool
    note: str
    created_at: str
    updated_at: str


class CreateAdmissionRequest(BaseModel):
    patient_id: str = Field(..., min_length=1, max_length=64)
    patient_name: str = Field(..., min_length=1, max_length=64)
    admission_date: str = Field(..., min_length=10, max_length=10,
                                description="YYYY-MM-DD")
    bed_number: Optional[str] = Field(default="", max_length=32)
    care_level: Optional[str] = Field(default="", max_length=32)
    monthly_fee: float = Field(default=0.0, ge=0)
    note: Optional[str] = Field(default="", max_length=512)


class CreateAdmissionResponse(_CodeMessage):
    admission: AdmissionResponse


class UpdateAdmissionRequest(BaseModel):
    patient_name: Optional[str] = Field(default=None, max_length=64)
    bed_number: Optional[str] = Field(default=None, max_length=32)
    care_level: Optional[str] = Field(default=None, max_length=32)
    monthly_fee: Optional[float] = Field(default=None, ge=0)
    admission_date: Optional[str] = Field(default=None, max_length=10)
    discharge_date: Optional[str] = Field(default=None, max_length=10)
    note: Optional[str] = Field(default=None, max_length=512)


class AdmissionListResponse(_CodeMessage):
    total: int
    admissions: List[AdmissionResponse] = Field(default_factory=list)


class DischargeRequest(BaseModel):
    discharge_date: str = Field(..., min_length=10, max_length=10,
                                description="YYYY-MM-DD")


# ── Bill ─────────────────────────────────────────────────
class BillResponse(BaseModel):
    bill_id: str
    admission_id: str
    patient_id: str
    patient_name: str
    billing_month: str
    bed_fee: float
    care_fee: float
    other_fee: float
    amount_due: float
    amount_paid: float
    amount_remaining: float
    status: str
    note: str
    created_at: str
    updated_at: str


class CreateBillRequest(BaseModel):
    admission_id: str = Field(..., min_length=1)
    billing_month: str = Field(..., min_length=7, max_length=7,
                               description="YYYY-MM")
    bed_fee: float = Field(default=0.0, ge=0)
    care_fee: float = Field(default=0.0, ge=0)
    other_fee: float = Field(default=0.0, ge=0)
    note: Optional[str] = Field(default="", max_length=512)


class CreateBillResponse(_CodeMessage):
    bill: BillResponse


class BillListResponse(_CodeMessage):
    total: int
    bills: List[BillResponse] = Field(default_factory=list)


class VoidBillRequest(BaseModel):
    reason: Optional[str] = Field(default="", max_length=256)


# ── Payment ──────────────────────────────────────────────
class PaymentResponse(BaseModel):
    payment_id: str
    bill_id: str
    amount: float
    method: str
    paid_at: str
    received_by: str
    note: str
    is_voided: bool
    voided_at: str
    voided_by: str
    void_reason: str
    created_at: str


class AddPaymentRequest(BaseModel):
    bill_id: str = Field(..., min_length=1)
    amount: float = Field(..., gt=0)
    method: str = Field(default="cash", max_length=16)
    received_by: Optional[str] = Field(default="", max_length=64)
    note: Optional[str] = Field(default="", max_length=256)
    paid_at: Optional[str] = Field(default=None, max_length=19,
                                   description="YYYY-MM-DD HH:MM:SS（可选）")


class AddPaymentResponse(_CodeMessage):
    payment: PaymentResponse


class PaymentListResponse(_CodeMessage):
    total: int
    payments: List[PaymentResponse] = Field(default_factory=list)


class VoidPaymentRequest(BaseModel):
    reason: Optional[str] = Field(default="", max_length=256)


# ── Summary ──────────────────────────────────────────────
class BillingSummaryResponse(_CodeMessage):
    billing_month: str
    total_due: float
    total_paid: float
    total_remaining: float
    by_status: dict


__all__ = [
    "AdmissionResponse",
    "CreateAdmissionRequest",
    "CreateAdmissionResponse",
    "UpdateAdmissionRequest",
    "AdmissionListResponse",
    "DischargeRequest",
    "BillResponse",
    "CreateBillRequest",
    "CreateBillResponse",
    "BillListResponse",
    "VoidBillRequest",
    "PaymentResponse",
    "AddPaymentRequest",
    "AddPaymentResponse",
    "PaymentListResponse",
    "VoidPaymentRequest",
    "BillingSummaryResponse",
]
