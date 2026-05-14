# -*- coding: utf-8 -*-
"""
@File    : app/routers/beds.py
@Desc    : 床位管理路由 —— 床位 CRUD、分配、释放、状态查询
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from app.middleware.auth import get_current_user, require_permission
from app.models.care_schemas import (
    BedAssign, BedCreate, BedListResponse, BedResponse, BedUpdate,
)
from app.services.audit_log import get_audit_log
from app.services.care_store import get_care_store
from app.services.permissions import PERM_EHR_WRITE, PERM_EHR_READ
from app.services.user_store import User

router = APIRouter()
audit = get_audit_log()


@router.post("/beds", response_model=BedResponse, summary="新增床位")
async def create_bed(
    payload: BedCreate,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    try:
        bed = store.create_bed(payload.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    audit.log("BED_CREATE", "", user.username, doc_id=bed["bed_id"],
              detail=f"新增床位: {bed['bed_number']}")
    logger.info(f"新增床位: {bed['bed_number']}, operator={user.username}")
    return BedResponse(**bed)


@router.get("/beds", response_model=BedListResponse, summary="查询床位列表")
async def list_beds(
    status_filter: str = None,
    building: str = None,
    user: User = Depends(require_permission(PERM_EHR_READ)),
):
    store = get_care_store()
    beds = store.list_beds(status=status_filter, building=building)
    return BedListResponse(code=200, total=len(beds), beds=[BedResponse(**b) for b in beds])


@router.get("/beds/{bed_id}", response_model=BedResponse, summary="查询单个床位")
async def get_bed(
    bed_id: str,
    user: User = Depends(require_permission(PERM_EHR_READ)),
):
    store = get_care_store()
    bed = store.get_bed(bed_id)
    if not bed:
        raise HTTPException(status_code=404, detail="床位不存在")
    return BedResponse(**bed)


@router.patch("/beds/{bed_id}", response_model=BedResponse, summary="修改床位信息")
async def update_bed(
    bed_id: str,
    payload: BedUpdate,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    try:
        bed = store.update_bed(bed_id, payload.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if not bed:
        raise HTTPException(status_code=404, detail="床位不存在")
    audit.log("BED_UPDATE", bed.get("patient_id", ""), user.username,
              doc_id=bed_id, detail=f"修改床位: {bed['bed_number']}")
    return BedResponse(**bed)


@router.post("/beds/{bed_id}/assign", response_model=BedResponse, summary="分配床位给老人")
async def assign_bed(
    bed_id: str,
    payload: BedAssign,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    bed = store.assign_bed(bed_id, payload.patient_id)
    if not bed:
        raise HTTPException(status_code=400, detail="床位不可分配（可能已被占用或不存在）")
    audit.log("BED_ASSIGN", payload.patient_id, user.username,
              doc_id=bed_id, detail=f"分配床位 {bed['bed_number']} 给患者 {payload.patient_id}")
    logger.info(f"床位分配: {bed['bed_number']} -> {payload.patient_id}, operator={user.username}")
    return BedResponse(**bed)


@router.post("/beds/{bed_id}/release", response_model=BedResponse, summary="释放床位")
async def release_bed(
    bed_id: str,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    bed = store.release_bed(bed_id)
    if not bed:
        raise HTTPException(status_code=404, detail="床位不存在")
    audit.log("BED_RELEASE", "", user.username, doc_id=bed_id,
              detail=f"释放床位: {bed['bed_number']}")
    return BedResponse(**bed)


@router.delete("/beds/{bed_id}", summary="删除床位")
async def delete_bed(
    bed_id: str,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    success = store.delete_bed(bed_id)
    if not success:
        raise HTTPException(status_code=400, detail="床位不存在或正在使用中，无法删除")
    audit.log("BED_DELETE", "", user.username, doc_id=bed_id, detail="删除床位")
    return {"code": 200, "message": "床位已删除"}
