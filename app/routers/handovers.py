# -*- coding: utf-8 -*-
"""
@File    : app/routers/handovers.py
@Desc    : 交接班管理路由 —— SBAR 格式交接记录创建、确认、查询
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.middleware.auth import get_current_user, require_permission
from app.models.care_schemas import (
    HandoverAcknowledge, HandoverCreate, HandoverListResponse, HandoverResponse,
)
from app.services.audit_log import get_audit_log
from app.services.care_store import get_care_store
from app.services.permissions import PERM_NURSING_TASKCARD
from app.services.user_store import User

router = APIRouter()
audit = get_audit_log()


@router.post("/handovers", response_model=HandoverResponse, summary="创建交接班记录(SBAR)")
async def create_handover(
    payload: HandoverCreate,
    user: User = Depends(require_permission(PERM_NURSING_TASKCARD)),
):
    store = get_care_store()
    data = payload.model_dump()
    # 尝试补充患者姓名（如果提供了 patient_id）
    if data.get("patient_id"):
        data["patient_name"] = _find_patient_name(data["patient_id"])
    handover = store.create_handover(data)
    audit.log("HANDOVER_CREATE", data.get("patient_id", ""), user.username,
              doc_id=handover["handover_id"],
              detail=f"交接班: {data['shift_from']} → {data['shift_to']} ({data.get('shift_type', '')})")
    logger.info(f"创建交接班: {handover['handover_id']}, {data['shift_from']}→{data['shift_to']}, operator={user.username}")
    return HandoverResponse(**handover)


@router.get("/handovers", response_model=HandoverListResponse, summary="查询交接班记录列表")
async def list_handovers(
    patient_id: str = None,
    status: str = None,
    limit: int = Query(default=50, le=200),
    user: User = Depends(require_permission(PERM_NURSING_TASKCARD)),
):
    store = get_care_store()
    handovers = store.list_handovers(patient_id=patient_id, status=status, limit=limit)
    return HandoverListResponse(
        code=200, total=len(handovers),
        handovers=[HandoverResponse(**h) for h in handovers],
    )


@router.get("/handovers/{handover_id}", response_model=HandoverResponse, summary="查询单条交接班记录")
async def get_handover(
    handover_id: str,
    user: User = Depends(require_permission(PERM_NURSING_TASKCARD)),
):
    store = get_care_store()
    handover = store.get_handover(handover_id)
    if not handover:
        raise HTTPException(status_code=404, detail="交接班记录不存在")
    return HandoverResponse(**handover)


@router.patch("/handovers/{handover_id}/acknowledge", response_model=HandoverResponse,
              summary="确认接班(接班人签收)")
async def acknowledge_handover(
    handover_id: str,
    payload: HandoverAcknowledge,
    user: User = Depends(require_permission(PERM_NURSING_TASKCARD)),
):
    store = get_care_store()
    handover = store.acknowledge_handover(
        handover_id,
        acknowledged_by=payload.acknowledged_by or user.username,
        note=payload.note or "",
    )
    if not handover:
        raise HTTPException(status_code=400, detail="交接班记录不存在或已确认")
    audit.log("HANDOVER_ACK", handover.get("patient_id", ""), user.username,
              doc_id=handover_id,
              detail=f"接班确认: {payload.acknowledged_by or user.username}")
    return HandoverResponse(**handover)


def _find_patient_name(patient_id: str) -> str:
    """尝试从 ChromaDB 获取患者姓名，失败则返回空串。"""
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
