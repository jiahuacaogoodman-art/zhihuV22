# -*- coding: utf-8 -*-
"""
@File    : app/routers/care_levels.py
@Desc    : 护理等级管理路由 —— 等级定义 CRUD、等级分配
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from app.middleware.auth import get_current_user, require_permission
from app.models.care_schemas import (
    CareLevelAssign, CareLevelCreate, CareLevelListResponse,
    CareLevelResponse, CareLevelUpdate,
)
from app.services.audit_log import get_audit_log
from app.services.care_store import get_care_store
from app.services.permissions import PERM_EHR_WRITE, PERM_EHR_READ
from app.services.user_store import User

router = APIRouter()
audit = get_audit_log()


@router.post("/care-levels", response_model=CareLevelResponse, summary="新增护理等级")
async def create_care_level(
    payload: CareLevelCreate,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    try:
        level = store.create_care_level(payload.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    audit.log("CARE_LEVEL_CREATE", "", user.username,
              doc_id=level["level_id"], detail=f"新增护理等级: {level['level_name']}")
    logger.info(f"新增护理等级: {level['level_key']} ({level['level_name']}), operator={user.username}")
    return CareLevelResponse(**level)


@router.get("/care-levels", response_model=CareLevelListResponse, summary="查询护理等级列表")
async def list_care_levels(
    user: User = Depends(require_permission(PERM_EHR_READ)),
):
    store = get_care_store()
    levels = store.list_care_levels()
    return CareLevelListResponse(
        code=200, total=len(levels),
        levels=[CareLevelResponse(**lv) for lv in levels],
    )


@router.get("/care-levels/{level_key}", response_model=CareLevelResponse, summary="查询单个护理等级")
async def get_care_level(
    level_key: str,
    user: User = Depends(require_permission(PERM_EHR_READ)),
):
    store = get_care_store()
    level = store.get_care_level_by_key(level_key)
    if not level:
        raise HTTPException(status_code=404, detail=f"护理等级 '{level_key}' 不存在")
    return CareLevelResponse(**level)


@router.patch("/care-levels/{level_key}", response_model=CareLevelResponse, summary="修改护理等级")
async def update_care_level(
    level_key: str,
    payload: CareLevelUpdate,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    level = store.update_care_level(level_key, payload.model_dump(exclude_unset=True))
    if not level:
        raise HTTPException(status_code=404, detail=f"护理等级 '{level_key}' 不存在")
    audit.log("CARE_LEVEL_UPDATE", "", user.username,
              doc_id=level["level_id"], detail=f"修改护理等级: {level['level_name']}")
    return CareLevelResponse(**level)


@router.delete("/care-levels/{level_key}", summary="删除护理等级")
async def delete_care_level(
    level_key: str,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    success = store.delete_care_level(level_key)
    if not success:
        raise HTTPException(status_code=404, detail=f"护理等级 '{level_key}' 不存在")
    audit.log("CARE_LEVEL_DELETE", "", user.username, detail=f"删除护理等级: {level_key}")
    return {"code": 200, "message": f"护理等级 '{level_key}' 已删除"}


@router.post("/care-levels/assign", summary="为老人分配/调整护理等级")
async def assign_care_level(
    payload: CareLevelAssign,
    user: User = Depends(require_permission(PERM_EHR_WRITE)),
):
    store = get_care_store()
    try:
        result = store.assign_care_level(
            patient_id=payload.patient_id,
            level_key=payload.level_key,
            reason=payload.reason or "",
            assessed_by=payload.assessed_by or user.username,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit.log("CARE_LEVEL_ASSIGN", payload.patient_id, user.username,
              detail=f"分配护理等级: {payload.level_key}, 原因: {payload.reason or '无'}")
    logger.info(f"护理等级分配: {payload.patient_id} -> {payload.level_key}, operator={user.username}")
    return {"code": 200, "message": "护理等级已分配", **result}


@router.get("/care-levels/patient/{patient_id}", summary="查询老人当前护理等级")
async def get_patient_care_level(
    patient_id: str,
    user: User = Depends(require_permission(PERM_EHR_READ)),
):
    store = get_care_store()
    result = store.get_patient_care_level(patient_id)
    if not result:
        return {"code": 200, "patient_id": patient_id, "level": None, "message": "该老人尚未分配护理等级"}
    return {"code": 200, "patient_id": patient_id, "level": result}
