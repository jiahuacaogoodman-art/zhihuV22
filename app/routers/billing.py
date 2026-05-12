# -*- coding: utf-8 -*-
"""
@File    : app/routers/billing.py
@Desc    : 账务管理路由：入住 / 账单 / 收款

接口一览
  POST   /api/billing/admissions                billing.write → 登记入住
  GET    /api/billing/admissions                billing.read  → 入住列表
  GET    /api/billing/admissions/{id}           billing.read  → 入住详情
  PATCH  /api/billing/admissions/{id}           billing.write → 修改入住
  POST   /api/billing/admissions/{id}/discharge billing.write → 办理出住
  DELETE /api/billing/admissions/{id}           billing.write → 删除入住（无账单时）

  POST   /api/billing/bills                    billing.write → 出账单
  GET    /api/billing/bills                    billing.read  → 账单列表
  GET    /api/billing/bills/{id}               billing.read  → 账单详情
  POST   /api/billing/bills/{id}/void          billing.write → 作废账单

  POST   /api/billing/payments                 billing.write → 登记收款
  GET    /api/billing/payments                 billing.read  → 收款列表
  POST   /api/billing/payments/{id}/void       billing.write → 作废收款

  GET    /api/billing/summary                  billing.read  → 汇总看板
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger

from app.middleware.auth import require_permission
from app.models.billing_schemas import (
    AddPaymentRequest,
    AddPaymentResponse,
    AdmissionListResponse,
    AdmissionResponse,
    BillingSummaryResponse,
    BillListResponse,
    BillResponse,
    CreateAdmissionRequest,
    CreateAdmissionResponse,
    CreateBillRequest,
    CreateBillResponse,
    DischargeRequest,
    PaymentListResponse,
    PaymentResponse,
    UpdateAdmissionRequest,
    VoidBillRequest,
    VoidPaymentRequest,
)
from app.services.billing_store import (
    AdmissionHasBillsError,
    AdmissionNotFoundError,
    BillingError,
    BillingStore,
    BillExistsError,
    BillNotFoundError,
    BillVoidedError,
    PaymentAlreadyVoidedError,
    PaymentExceedsRemainingError,
    PaymentNotFoundError,
)
from app.services.permissions import PERM_BILLING_READ, PERM_BILLING_WRITE
from app.services.user_store import User

router = APIRouter()


def _get_billing_store(request: Request) -> BillingStore:
    store = getattr(request.app.state, "billing_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="BillingStore 尚未初始化",
        )
    return store


# ── Admission 入住 ───────────────────────────────────────
@router.post(
    "/billing/admissions",
    response_model=CreateAdmissionResponse,
    summary="登记入住（需要 billing.write 权限）",
)
async def create_admission(
    payload: CreateAdmissionRequest,
    request: Request,
    user: User = Depends(require_permission(PERM_BILLING_WRITE)),
):
    store = _get_billing_store(request)
    try:
        adm = store.create_admission(
            patient_id=payload.patient_id,
            patient_name=payload.patient_name,
            admission_date=payload.admission_date,
            bed_number=payload.bed_number or "",
            care_level=payload.care_level or "",
            monthly_fee=payload.monthly_fee,
            note=payload.note or "",
        )
    except (ValueError, BillingError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"user='{user.username}' 登记入住 adm={adm.admission_id} "
                f"patient={adm.patient_id}")
    return CreateAdmissionResponse(
        code=200,
        message="入住登记成功",
        admission=AdmissionResponse(**adm.to_dict()),
    )


@router.get(
    "/billing/admissions",
    response_model=AdmissionListResponse,
    summary="查询入住列表（需要 billing.read 权限）",
)
async def list_admissions(
    request: Request,
    patient_id: Optional[str] = None,
    include_discharged: bool = True,
    _user: User = Depends(require_permission(PERM_BILLING_READ)),
):
    store = _get_billing_store(request)
    adms = store.list_admissions(patient_id=patient_id,
                                 include_discharged=include_discharged)
    return AdmissionListResponse(
        code=200,
        total=len(adms),
        admissions=[AdmissionResponse(**a.to_dict()) for a in adms],
    )


@router.get(
    "/billing/admissions/{admission_id}",
    response_model=AdmissionResponse,
    summary="查询入住详情（需要 billing.read 权限）",
)
async def get_admission(
    admission_id: str,
    request: Request,
    _user: User = Depends(require_permission(PERM_BILLING_READ)),
):
    store = _get_billing_store(request)
    adm = store.get_admission(admission_id)
    if adm is None:
        raise HTTPException(status_code=404, detail="入住记录不存在")
    return AdmissionResponse(**adm.to_dict())


@router.patch(
    "/billing/admissions/{admission_id}",
    response_model=AdmissionResponse,
    summary="修改入住信息（需要 billing.write 权限）",
)
async def update_admission(
    admission_id: str,
    payload: UpdateAdmissionRequest,
    request: Request,
    user: User = Depends(require_permission(PERM_BILLING_WRITE)),
):
    store = _get_billing_store(request)
    try:
        adm = store.update_admission(
            admission_id,
            patient_name=payload.patient_name,
            bed_number=payload.bed_number,
            care_level=payload.care_level,
            monthly_fee=payload.monthly_fee,
            admission_date=payload.admission_date,
            discharge_date=payload.discharge_date,
            note=payload.note,
        )
    except AdmissionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, BillingError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"user='{user.username}' 修改入住 adm={admission_id}")
    return AdmissionResponse(**adm.to_dict())


@router.post(
    "/billing/admissions/{admission_id}/discharge",
    response_model=AdmissionResponse,
    summary="办理出住（需要 billing.write 权限）",
)
async def discharge_admission(
    admission_id: str,
    payload: DischargeRequest,
    request: Request,
    user: User = Depends(require_permission(PERM_BILLING_WRITE)),
):
    store = _get_billing_store(request)
    try:
        adm = store.discharge(admission_id, payload.discharge_date)
    except AdmissionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, BillingError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"user='{user.username}' 办理出住 adm={admission_id}")
    return AdmissionResponse(**adm.to_dict())


@router.delete(
    "/billing/admissions/{admission_id}",
    summary="删除入住记录（需要 billing.write，仅当无账单时允许）",
)
async def delete_admission(
    admission_id: str,
    request: Request,
    user: User = Depends(require_permission(PERM_BILLING_WRITE)),
):
    store = _get_billing_store(request)
    try:
        store.delete_admission(admission_id)
    except AdmissionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except AdmissionHasBillsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    logger.info(f"user='{user.username}' 删除入住 adm={admission_id}")
    return {"code": 200, "message": "入住记录已删除"}


# ── Bill 账单 ────────────────────────────────────────────
@router.post(
    "/billing/bills",
    response_model=CreateBillResponse,
    summary="出具月度账单（需要 billing.write 权限）",
)
async def create_bill(
    payload: CreateBillRequest,
    request: Request,
    user: User = Depends(require_permission(PERM_BILLING_WRITE)),
):
    store = _get_billing_store(request)
    try:
        bill = store.create_bill(
            admission_id=payload.admission_id,
            billing_month=payload.billing_month,
            bed_fee=payload.bed_fee,
            care_fee=payload.care_fee,
            other_fee=payload.other_fee,
            note=payload.note or "",
        )
    except AdmissionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BillExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except (ValueError, BillingError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"user='{user.username}' 出账单 bill={bill.bill_id} "
                f"month={payload.billing_month}")
    return CreateBillResponse(
        code=200,
        message="账单已出具",
        bill=BillResponse(**bill.to_dict()),
    )


@router.get(
    "/billing/bills",
    response_model=BillListResponse,
    summary="查询账单列表（需要 billing.read 权限）",
)
async def list_bills(
    request: Request,
    admission_id: Optional[str] = None,
    patient_id: Optional[str] = None,
    billing_month: Optional[str] = None,
    bill_status: Optional[str] = None,
    _user: User = Depends(require_permission(PERM_BILLING_READ)),
):
    store = _get_billing_store(request)
    try:
        bills = store.list_bills(
            admission_id=admission_id,
            patient_id=patient_id,
            billing_month=billing_month,
            status=bill_status,
        )
    except (ValueError, BillingError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return BillListResponse(
        code=200,
        total=len(bills),
        bills=[BillResponse(**b.to_dict()) for b in bills],
    )


@router.get(
    "/billing/bills/{bill_id}",
    response_model=BillResponse,
    summary="查询账单详情（需要 billing.read 权限）",
)
async def get_bill(
    bill_id: str,
    request: Request,
    _user: User = Depends(require_permission(PERM_BILLING_READ)),
):
    store = _get_billing_store(request)
    bill = store.get_bill(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="账单不存在")
    return BillResponse(**bill.to_dict())


@router.post(
    "/billing/bills/{bill_id}/void",
    response_model=BillResponse,
    summary="作废账单（需要 billing.write 权限）",
)
async def void_bill(
    bill_id: str,
    payload: VoidBillRequest,
    request: Request,
    user: User = Depends(require_permission(PERM_BILLING_WRITE)),
):
    store = _get_billing_store(request)
    try:
        bill = store.void_bill(bill_id, reason=payload.reason or "")
    except BillNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BillVoidedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BillingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"user='{user.username}' 作废账单 bill={bill_id}")
    return BillResponse(**bill.to_dict())


# ── Payment 收款 ─────────────────────────────────────────
@router.post(
    "/billing/payments",
    response_model=AddPaymentResponse,
    summary="登记收款（需要 billing.write 权限）",
)
async def add_payment(
    payload: AddPaymentRequest,
    request: Request,
    user: User = Depends(require_permission(PERM_BILLING_WRITE)),
):
    store = _get_billing_store(request)
    try:
        pay = store.add_payment(
            bill_id=payload.bill_id,
            amount=payload.amount,
            method=payload.method or "cash",
            received_by=payload.received_by or user.username,
            note=payload.note or "",
            paid_at=payload.paid_at,
        )
    except BillNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BillVoidedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PaymentExceedsRemainingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (ValueError, BillingError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"user='{user.username}' 登记收款 pay={pay.payment_id} "
                f"bill={payload.bill_id} amount={payload.amount}")
    return AddPaymentResponse(
        code=200,
        message="收款登记成功",
        payment=PaymentResponse(**pay.to_dict()),
    )


@router.get(
    "/billing/payments",
    response_model=PaymentListResponse,
    summary="查询收款流水（需要 billing.read 权限）",
)
async def list_payments(
    request: Request,
    bill_id: Optional[str] = None,
    include_voided: bool = True,
    _user: User = Depends(require_permission(PERM_BILLING_READ)),
):
    store = _get_billing_store(request)
    payments = store.list_payments(bill_id=bill_id, include_voided=include_voided)
    return PaymentListResponse(
        code=200,
        total=len(payments),
        payments=[PaymentResponse(**p.to_dict()) for p in payments],
    )


@router.post(
    "/billing/payments/{payment_id}/void",
    response_model=PaymentResponse,
    summary="作废收款（需要 billing.write 权限）",
)
async def void_payment(
    payment_id: str,
    payload: VoidPaymentRequest,
    request: Request,
    user: User = Depends(require_permission(PERM_BILLING_WRITE)),
):
    store = _get_billing_store(request)
    try:
        pay = store.void_payment(
            payment_id,
            voided_by=user.username,
            reason=payload.reason or "",
        )
    except PaymentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PaymentAlreadyVoidedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BillingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"user='{user.username}' 作废收款 pay={payment_id}")
    return PaymentResponse(**pay.to_dict())


# ── Summary 汇总 ─────────────────────────────────────────
@router.get(
    "/billing/summary",
    response_model=BillingSummaryResponse,
    summary="账务看板汇总（需要 billing.read 权限）",
)
async def billing_summary(
    request: Request,
    billing_month: Optional[str] = None,
    _user: User = Depends(require_permission(PERM_BILLING_READ)),
):
    store = _get_billing_store(request)
    try:
        data = store.summary(billing_month=billing_month)
    except BillingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return BillingSummaryResponse(code=200, **data)
