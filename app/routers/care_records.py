# -*- coding: utf-8 -*-
"""
@File    : app/routers/care_records.py
@Desc    : 护理记录留痕路由 —— 护理操作记录、生命体征、质控追溯
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.middleware.auth import get_current_user, require_permission
from app.models.care_schemas import (
    CareRecordCreate, CareRecordListResponse, CareRecordResponse,
)
from app.services.audit_log import get_audit_log
from app.services.care_store import get_care_store
from app.services.permissions import PERM_NURSING_TASKCARD, PERM_EHR_READ
from app.services.user_store import User

router = APIRouter()
audit = get_audit_log()


@router.post("/care-records", response_model=CareRecordResponse, summary="创建护理记录")
async def create_care_record(
    payload: CareRecordCreate,
    user: User = Depends(require_permission(PERM_NURSING_TASKCARD)),
):
    store = get_care_store()
    data = payload.model_dump()
    # 补充患者姓名
    data["patient_name"] = _find_patient_name(data["patient_id"])
    # 如果未指定记录人，使用当前用户
    if not data.get("recorded_by"):
        data["recorded_by"] = user.display_name or user.username
    record = store.create_care_record(data)
    audit.log("CARE_RECORD_CREATE", data["patient_id"], user.username,
              doc_id=record["record_id"],
              detail=f"护理记录: {data['record_type']} by {data.get('recorded_by', user.username)}")
    logger.info(
        f"创建护理记录: {record['record_id']}, type={data['record_type']}, "
        f"patient={data['patient_id']}, operator={user.username}"
    )
    return CareRecordResponse(**record)


@router.get("/care-records", response_model=CareRecordListResponse, summary="查询护理记录列表")
async def list_care_records(
    patient_id: str = None,
    record_type: str = None,
    shift: str = None,
    limit: int = Query(default=100, le=500),
    user: User = Depends(require_permission(PERM_EHR_READ)),
):
    store = get_care_store()
    records = store.list_care_records(
        patient_id=patient_id, record_type=record_type, shift=shift, limit=limit
    )
    return CareRecordListResponse(
        code=200, total=len(records),
        records=[CareRecordResponse(**r) for r in records],
    )


@router.get("/care-records/{record_id}", response_model=CareRecordResponse, summary="查询单条护理记录")
async def get_care_record(
    record_id: str,
    user: User = Depends(require_permission(PERM_EHR_READ)),
):
    store = get_care_store()
    record = store.get_care_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="护理记录不存在")
    return CareRecordResponse(**record)


@router.get("/care-records/patient/{patient_id}", response_model=CareRecordListResponse,
            summary="查询某患者的护理记录")
async def get_patient_care_records(
    patient_id: str,
    record_type: str = None,
    limit: int = Query(default=50, le=200),
    user: User = Depends(require_permission(PERM_EHR_READ)),
):
    store = get_care_store()
    records = store.list_care_records(patient_id=patient_id, record_type=record_type, limit=limit)
    audit.log("CARE_RECORD_READ", patient_id, user.username,
              detail=f"查询护理记录列表, type={record_type or 'all'}, count={len(records)}")
    return CareRecordListResponse(
        code=200, total=len(records),
        records=[CareRecordResponse(**r) for r in records],
    )


def _find_patient_name(patient_id: str) -> str:
    """尝试从 ChromaDB 获取患者姓名"""
    try:
        from main import app_state
        collection = app_state.get("db_collection")
        if collection:
            result = collection.get(
                where={"patient_id": {"$eq": patient_id}},
                include=["metadatas"],
            )
            for meta in result.get("metadatas", []):
                if meta and meta.get("name"):
                    return meta["name"]
    except Exception:
        pass
    return ""
