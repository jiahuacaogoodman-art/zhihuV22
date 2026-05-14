# -*- coding: utf-8 -*-
"""
@File    : app/routers/incidents.py
@Desc    : 异常事件上报路由 —— 事件上报、处理流程跟踪、统计分析
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.middleware.auth import get_current_user, require_permission
from app.models.care_schemas import (
    IncidentCreate, IncidentListResponse, IncidentResponse,
    IncidentStats, IncidentUpdate,
)
from app.services.audit_log import get_audit_log
from app.services.care_store import get_care_store
from app.services.permissions import PERM_NURSING_TASKCARD, PERM_EHR_AUDIT_READ
from app.services.user_store import User

router = APIRouter()
audit = get_audit_log()


@router.post("/incidents", response_model=IncidentResponse, summary="上报异常事件")
async def create_incident(
    payload: IncidentCreate,
    user: User = Depends(require_permission(PERM_NURSING_TASKCARD)),
):
    store = get_care_store()
    data = payload.model_dump()
    # 补充患者姓名
    data["patient_name"] = _find_patient_name(data["patient_id"])
    # 如果未指定上报人，使用当前用户
    if not data.get("reporter"):
        data["reporter"] = user.display_name or user.username
    incident = store.create_incident(data)
    audit.log("INCIDENT_CREATE", data["patient_id"], user.username,
              doc_id=incident["incident_id"],
              detail=f"上报异常事件: {data['incident_type']} ({data.get('severity', 'minor')})")
    logger.warning(
        f"异常事件上报: [{incident['severity']}] {incident['incident_type']} "
        f"patient={data['patient_id']}, reporter={data.get('reporter')}"
    )
    return IncidentResponse(**incident)


@router.get("/incidents", response_model=IncidentListResponse, summary="查询异常事件列表")
async def list_incidents(
    patient_id: str = None,
    severity: str = None,
    status: str = None,
    limit: int = Query(default=100, le=500),
    user: User = Depends(require_permission(PERM_NURSING_TASKCARD)),
):
    store = get_care_store()
    incidents = store.list_incidents(
        patient_id=patient_id, severity=severity, status=status, limit=limit
    )
    return IncidentListResponse(
        code=200, total=len(incidents),
        incidents=[IncidentResponse(**inc) for inc in incidents],
    )


@router.get("/incidents/stats", response_model=IncidentStats, summary="异常事件统计(院长/护士长)")
async def get_incident_stats(
    days: int = Query(default=30, ge=1, le=365),
    user: User = Depends(require_permission(PERM_EHR_AUDIT_READ)),
):
    store = get_care_store()
    stats = store.get_incident_stats(days=days)
    return IncidentStats(**stats)


@router.get("/incidents/{incident_id}", response_model=IncidentResponse, summary="查询单个异常事件")
async def get_incident(
    incident_id: str,
    user: User = Depends(require_permission(PERM_NURSING_TASKCARD)),
):
    store = get_care_store()
    incident = store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    return IncidentResponse(**incident)


@router.patch("/incidents/{incident_id}", response_model=IncidentResponse, summary="更新异常事件(处理/关闭)")
async def update_incident(
    incident_id: str,
    payload: IncidentUpdate,
    user: User = Depends(require_permission(PERM_NURSING_TASKCARD)),
):
    store = get_care_store()
    data = payload.model_dump(exclude_unset=True)
    # 如果标记为 resolved 且未指定处理人，使用当前用户
    if data.get("status") == "resolved" and not data.get("resolved_by"):
        data["resolved_by"] = user.display_name or user.username
    if data.get("status") == "resolved" and not data.get("resolved_at"):
        from datetime import datetime
        data["resolved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    incident = store.update_incident(incident_id, data)
    if not incident:
        raise HTTPException(status_code=404, detail="异常事件不存在")
    audit.log("INCIDENT_UPDATE", incident["patient_id"], user.username,
              doc_id=incident_id,
              detail=f"更新异常事件: status={incident['status']}, severity={incident['severity']}")
    return IncidentResponse(**incident)


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
