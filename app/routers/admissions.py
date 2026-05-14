# -*- coding: utf-8 -*-
"""
@File    : app/routers/admissions.py
@Desc    : 入住流程路由 —— 完整入住生命周期管理

流程：咨询→评估→签约→缴费→入住→离院
每一步都是独立端点，前端可分步操作也可一键联动。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.middleware.auth import require_permission
from app.models.admission_schemas import (
    VALID_TRANSITIONS,
    AdmissionCreateRequest, AdmissionListResponse, AdmissionResponse,
    AdmissionTimelineEntry, AdmissionTimelineResponse,
    AssessmentResponse, AssessmentSubmitRequest,
    ContractCreateRequest, ContractResponse,
    DischargeRequest,
    MoveInRequest,
    PaymentCreateRequest, PaymentListResponse, PaymentResponse,
    StatusChangeRequest,
)
from app.services.audit_log import get_audit_log
from app.services.care_store import get_care_store
from app.services.permissions import PERM_EHR_WRITE, PERM_EHR_READ
from app.services.user_store import User

router = APIRouter()
audit = get_audit_log()


# ================================================================
# 入住申请 CRUD
# ================================================================

@router.post("/admissions", response_model=AdmissionResponse, summary="创建入住申请(咨询登记)")
async def create_admission(
    payload: AdmissionCreateRequest,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    admission = store.create_admission(payload.model_dump(), operator=user.username)
    audit.log("ADMISSION_CREATE", "", user.username,
              doc_id=admission["admission_id"],
              detail=f"创建入住申请: {payload.applicant_name}")
    logger.info(f"入住申请创建: {admission['admission_id']}, 申请人={payload.applicant_name}, operator={user.username}")
    return AdmissionResponse(**admission)


@router.get("/admissions", response_model=AdmissionListResponse, summary="查询入住申请列表")
async def list_admissions(
    status: str = None,
    limit: int = Query(default=100, le=500),
    user: User = Depends(require_permission(PERM_EHR_READ)),
):
    store = get_care_store()
    admissions = store.list_admissions(status=status, limit=limit)
    return AdmissionListResponse(
        code=200, total=len(admissions),
        admissions=[AdmissionResponse(**a) for a in admissions],
    )


@router.get("/admissions/{admission_id}", response_model=AdmissionResponse, summary="查询单个入住申请")
async def get_admission(
    admission_id: str,
    user: User = Depends(require_permission(PERM_EHR_READ)),
):
    store = get_care_store()
    admission = store.get_admission(admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="入住申请不存在")
    return AdmissionResponse(**admission)


# ================================================================
# 步骤 1：评估
# ================================================================

@router.post("/admissions/{admission_id}/assess", response_model=AssessmentResponse,
             summary="提交评估结果(评估→已评估/退回)")
async def submit_assessment(
    admission_id: str,
    payload: AssessmentSubmitRequest,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    admission = store.get_admission(admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="入住申请不存在")
    # 状态校验：只能从 inquiry/assessing 提交评估
    if admission["status"] not in ("inquiry", "assessing"):
        raise HTTPException(status_code=400,
                            detail=f"当前状态 '{admission['status']}' 不允许提交评估，需要 inquiry 或 assessing 状态")
    # 先将状态推进到 assessing（如果当前是 inquiry）
    if admission["status"] == "inquiry":
        store.update_admission_status(admission_id, "assessing",
                                      operator=user.username, detail="开始评估")
    data = payload.model_dump()
    if not data.get("assessor"):
        data["assessor"] = user.username
    assessment = store.create_assessment(admission_id, data, operator=user.username)
    audit.log("ADMISSION_ASSESS", "", user.username,
              doc_id=admission_id,
              detail=f"评估完成: {'通过' if payload.approved else '未通过'}, 建议等级={payload.recommended_level}")
    logger.info(f"入住评估: {admission_id}, approved={payload.approved}, level={payload.recommended_level}")
    return AssessmentResponse(**assessment)


# ================================================================
# 步骤 2：签约
# ================================================================

@router.post("/admissions/{admission_id}/contract", response_model=ContractResponse,
             summary="签署合同(已评估→已签约)")
async def create_contract(
    admission_id: str,
    payload: ContractCreateRequest,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    admission = store.get_admission(admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="入住申请不存在")
    # 状态校验
    if admission["status"] not in ("assessed", "contracting"):
        raise HTTPException(status_code=400,
                            detail=f"当前状态 '{admission['status']}' 不允许签约，需要 assessed 或 contracting 状态")
    # 推进到 contracting
    if admission["status"] == "assessed":
        store.update_admission_status(admission_id, "contracting",
                                      operator=user.username, detail="开始签约")
    contract = store.create_contract(admission_id, payload.model_dump(), operator=user.username)
    audit.log("ADMISSION_CONTRACT", "", user.username,
              doc_id=admission_id,
              detail=f"合同签署: {contract['contract_number']}, 月费={payload.monthly_fee}")
    logger.info(f"合同签署: {admission_id}, contract={contract['contract_id']}")
    return ContractResponse(**contract)


# ================================================================
# 步骤 3：缴费
# ================================================================

@router.post("/admissions/{admission_id}/pay", response_model=PaymentResponse,
             summary="记录缴费(已签约→已缴费)")
async def create_payment(
    admission_id: str,
    payload: PaymentCreateRequest,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    admission = store.get_admission(admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="入住申请不存在")
    # 状态校验
    if admission["status"] not in ("contracted", "paying"):
        raise HTTPException(status_code=400,
                            detail=f"当前状态 '{admission['status']}' 不允许缴费，需要 contracted 或 paying 状态")
    # 推进到 paying
    if admission["status"] == "contracted":
        store.update_admission_status(admission_id, "paying",
                                      operator=user.username, detail="开始缴费")
    payment = store.create_payment(admission_id, payload.model_dump(), operator=user.username)
    audit.log("ADMISSION_PAY", "", user.username,
              doc_id=admission_id,
              detail=f"缴费: {payload.amount}元, 方式={payload.payment_method}")
    logger.info(f"缴费完成: {admission_id}, amount={payload.amount}")
    return PaymentResponse(**payment)


@router.get("/admissions/{admission_id}/payments", response_model=PaymentListResponse,
            summary="查询入住申请的缴费记录")
async def list_payments(
    admission_id: str,
    user: User = Depends(require_permission(PERM_EHR_READ)),
):
    store = get_care_store()
    payments = store.get_payments_by_admission(admission_id)
    return PaymentListResponse(code=200, total=len(payments),
                               payments=[PaymentResponse(**p) for p in payments])


# ================================================================
# 步骤 4：办理入住
# ================================================================

@router.post("/admissions/{admission_id}/move-in", response_model=AdmissionResponse,
             summary="办理入住(已缴费→已入住，自动分配床位+护理等级+创建档案)")
async def move_in(
    admission_id: str,
    payload: MoveInRequest,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    admission = store.get_admission(admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="入住申请不存在")
    # 状态校验
    if admission["status"] not in ("paid", "moving_in"):
        raise HTTPException(status_code=400,
                            detail=f"当前状态 '{admission['status']}' 不允许办理入住，需要 paid 或 moving_in 状态")
    # 推进到 moving_in
    if admission["status"] == "paid":
        store.update_admission_status(admission_id, "moving_in",
                                      operator=user.username, detail="开始办理入住")

    result = store.move_in(
        admission_id=admission_id,
        bed_id=payload.bed_id,
        care_level_key=payload.care_level_key,
        admission_date=payload.admission_date,
        operator=user.username,
    )
    if not result:
        raise HTTPException(status_code=400,
                            detail="入住办理失败：床位不可用或不存在")

    # 同步创建老人档案到 ChromaDB（与 EHR 模块联动）
    _sync_ehr_profile(result, payload.primary_nurse)

    audit.log("ADMISSION_MOVE_IN", result.get("patient_id", ""), user.username,
              doc_id=admission_id,
              detail=f"入住: 床位={result.get('bed_number')}, 等级={result.get('care_level_key')}")
    logger.info(f"入住完成: {admission_id}, patient={result.get('patient_id')}, bed={result.get('bed_number')}")
    return AdmissionResponse(**result)


# ================================================================
# 步骤 5：离院
# ================================================================

@router.post("/admissions/{admission_id}/discharge", response_model=AdmissionResponse,
             summary="办理离院(已入住→已离院，自动释放床位)")
async def discharge(
    admission_id: str,
    payload: DischargeRequest,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    admission = store.get_admission(admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="入住申请不存在")
    if admission["status"] != "active":
        raise HTTPException(status_code=400,
                            detail=f"当前状态 '{admission['status']}' 不允许离院，需要 active 状态")
    result = store.discharge(admission_id, payload.model_dump(), operator=user.username)
    if not result:
        raise HTTPException(status_code=500, detail="离院办理失败")
    audit.log("ADMISSION_DISCHARGE", admission.get("patient_id", ""), user.username,
              doc_id=admission_id,
              detail=f"离院: {payload.discharge_reason or '正常离院'}")
    logger.info(f"离院完成: {admission_id}")
    return AdmissionResponse(**result)


# ================================================================
# 通用：状态变更 / 时间线
# ================================================================

@router.patch("/admissions/{admission_id}/status", response_model=AdmissionResponse,
              summary="手动变更入住申请状态(含退回/取消)")
async def change_status(
    admission_id: str,
    payload: StatusChangeRequest,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    admission = store.get_admission(admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="入住申请不存在")
    current = admission["status"]
    target = payload.target_status
    # 校验合法转换
    allowed = VALID_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise HTTPException(status_code=400,
                            detail=f"不允许从 '{current}' 变更到 '{target}'。"
                                   f"允许的目标状态: {allowed}")
    result = store.update_admission_status(
        admission_id, target,
        operator=user.username,
        detail=payload.reason or f"手动变更: {current}→{target}",
    )
    if not result:
        raise HTTPException(status_code=500, detail="状态变更失败")
    audit.log("ADMISSION_STATUS_CHANGE", admission.get("patient_id", ""), user.username,
              doc_id=admission_id, detail=f"状态变更: {current}→{target}")
    return AdmissionResponse(**result)


@router.get("/admissions/{admission_id}/timeline", response_model=AdmissionTimelineResponse,
            summary="查询入住流程时间线")
async def get_timeline(
    admission_id: str,
    user: User = Depends(require_permission(PERM_EHR_READ)),
):
    store = get_care_store()
    admission = store.get_admission(admission_id)
    if not admission:
        raise HTTPException(status_code=404, detail="入住申请不存在")
    entries = store.get_admission_timeline(admission_id)
    return AdmissionTimelineResponse(
        code=200, admission_id=admission_id,
        timeline=[AdmissionTimelineEntry(**e) for e in entries],
    )


# ================================================================
# 辅助：联动创建 EHR 档案
# ================================================================

def _sync_ehr_profile(admission: dict, primary_nurse: str = None) -> None:
    """入住完成后，同步创建/更新老人档案到 ChromaDB。
    这里做 best-effort，失败不阻断入住流程。"""
    try:
        from main import app_state
        collection = app_state.get("db_collection")
        embedding_fn = app_state.get("embedding_function")
        if not collection or not embedding_fn:
            logger.warning("ChromaDB 不可用，跳过档案同步")
            return

        patient_id = admission.get("patient_id", "")
        if not patient_id:
            return

        # 构建档案文本
        name = admission.get("applicant_name", "")
        parts = [
            f"【老人档案】姓名：{name}，编号：{patient_id}",
            f"性别：{admission.get('applicant_gender', '')}",
            f"年龄：{admission.get('applicant_age', '')}",
            f"入住日期：{admission.get('actual_admission_date', '')}",
            f"床位号：{admission.get('bed_number', '')}",
            f"护理等级：{admission.get('care_level_key', '')}",
        ]
        if admission.get("health_summary"):
            parts.append(f"健康摘要：{admission['health_summary']}")
        if admission.get("care_needs"):
            parts.append(f"护理需求：{admission['care_needs']}")
        if admission.get("assessment_conclusion"):
            parts.append(f"评估结论：{admission['assessment_conclusion']}")

        document = "；".join(parts)

        # metadata
        from app.services.pii_crypto import encrypt_pii_fields
        meta = {
            "patient_id": patient_id,
            "name": name,
            "gender": admission.get("applicant_gender") or "",
            "age": admission.get("applicant_age") or 0,
            "admission_date": admission.get("actual_admission_date") or "",
            "bed_number": admission.get("bed_number") or "",
            "care_level": admission.get("care_level_key") or "",
            "primary_nurse": primary_nurse or "",
            "emergency_contact": admission.get("guardian_name") or "",
            "emergency_phone": admission.get("guardian_phone") or "",
            "emergency_relation": admission.get("guardian_relation") or "",
            "id_card": admission.get("applicant_id_card") or "",
            "doc_type": "profile",
            "medical_history": admission.get("health_summary") or "",
            "notes": admission.get("care_needs") or "",
        }
        meta = encrypt_pii_fields(meta)

        # 生成 embedding
        embedding = embedding_fn.encode(document).tolist()

        # 检查是否已存在
        doc_id = f"profile_{patient_id}"
        try:
            existing = collection.get(ids=[doc_id])
            if existing and existing.get("ids"):
                collection.update(ids=[doc_id], documents=[document],
                                  metadatas=[meta], embeddings=[embedding])
                logger.info(f"更新 EHR 档案: {patient_id}")
            else:
                collection.add(ids=[doc_id], documents=[document],
                               metadatas=[meta], embeddings=[embedding])
                logger.info(f"创建 EHR 档案: {patient_id}")
        except Exception:
            collection.add(ids=[doc_id], documents=[document],
                           metadatas=[meta], embeddings=[embedding])
            logger.info(f"创建 EHR 档案: {patient_id}")

    except Exception as e:
        logger.warning(f"同步 EHR 档案失败(不影响入住): {e}")
