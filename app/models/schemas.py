# -*- coding: utf-8 -*-
"""
@File    : app/models/schemas.py
@Desc    : 全量请求/响应 Pydantic Schema 定义。

            为什么这个文件单独存在：
            ehr.py / nursing.py 里大量使用 `from app.models.schemas import ...`，
            但此前 schemas 文件在仓库里缺失，导致服务启动即 ImportError。
            本文件按调用点严格反推字段：
              - 所有"字段可 None + _build_* 里用 .get() 兜底"的字段 → Optional
              - 所有 `payload.x or default` 的用法 → Optional
              - EHRRecord 用在 list_patients 返回里再 `EHRRecord(**item)` 回注，
                所以除 patient_id / name 外全部 Optional，并允许额外字段放宽校验
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================
# 通用混入：统一 code / message 响应形态
# ============================================================
class _CodeMessage(BaseModel):
    code: int = 200
    message: Optional[str] = None


# ============================================================
# EHR 档案：请求 / 响应
# ============================================================
class _EHRProfileBase(BaseModel):
    """所有档案字段（除 patient_id / name 外均可选）的共同基础。

    注意：Chroma metadata 只支持 str/int/float/bool，
    所以这里不使用 dict / list 字段；结构化扩展走 notes。
    """

    model_config = ConfigDict(extra="ignore")

    age: Optional[int] = Field(default=None, ge=0, le=150)
    gender: Optional[str] = None
    birth_date: Optional[str] = None
    id_card: Optional[str] = None
    admission_date: Optional[str] = None

    emergency_contact: Optional[str] = None
    emergency_phone: Optional[str] = None
    emergency_relation: Optional[str] = None

    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    blood_type: Optional[str] = None

    care_level: Optional[str] = None
    bed_number: Optional[str] = None
    primary_nurse: Optional[str] = None

    medical_history: Optional[str] = None
    allergy: Optional[str] = None
    diet_restriction: Optional[str] = None

    notes: Optional[str] = None


class EHRAddRequest(_EHRProfileBase):
    """POST /api/ehr/add | POST /api/ehr/patients"""

    patient_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=64)


class EHRAddResponse(_CodeMessage):
    patient_id: str
    doc_id: str


class EHRUpdateRequest(_EHRProfileBase):
    """PUT /api/ehr/patients/{patient_id} 的 body。
    路由里使用了 `model_dump(exclude_unset=True)`，要求 name 也允许缺省。"""

    patient_id: str = Field(..., min_length=1, max_length=64)
    name: Optional[str] = None


class EHRUpdateResponse(_CodeMessage):
    patient_id: str
    updated_count: int


class EHRDeleteRequest(BaseModel):
    patient_id: str = Field(..., min_length=1, max_length=64)


class EHRDeleteResponse(_CodeMessage):
    patient_id: str
    deleted_count: int


class EHRRecord(_EHRProfileBase):
    """档案明细。用于 list / get / 旧版 EHRListResponse.records。

    `list_patients` 里用 `_meta_to_record(...).model_dump()` 再包一圈
    `EHRRecord(**item)`，所以即便某些字段不存在也要放行。"""

    model_config = ConfigDict(extra="ignore")

    doc_id: Optional[str] = None
    patient_id: str
    name: Optional[str] = ""


class EHRListResponse(_CodeMessage):
    total: int
    records: List[EHRRecord] = Field(default_factory=list)


# ============================================================
# RAG 决策：请求 / 响应
# ============================================================
class NursingDecisionRequest(BaseModel):
    """POST /api/nursing/decision | /api/nursing/decision/stream"""

    patient_id: str = Field(..., min_length=1)
    symptom: str = Field(..., min_length=1)
    # 路由里 `payload.n_results or 5` → 允许不传
    n_results: Optional[int] = Field(default=None, ge=1, le=20)


class EvidenceItem(BaseModel):
    """对应 services/retrieval.py 里的 Evidence dataclass，字段完全一致。
    路由里 `EvidenceItem(**e.to_dict())` 需要能直接解构。"""

    model_config = ConfigDict(extra="ignore")

    evidence_id: str
    doc_id: str
    source_type: str
    source_label: str
    snippet: str
    score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DecisionMemoryItem(BaseModel):
    """对应 services/decision_memory.py 里 `_to_dict()` 的返回结构。"""

    model_config = ConfigDict(extra="ignore")

    decision_id: str
    patient_id: Optional[str] = None
    patient_name: Optional[str] = None
    timestamp: Optional[str] = None
    symptom: Optional[str] = None
    advice_preview: Optional[str] = None
    event_type: Optional[str] = None
    risk_level: Optional[str] = None
    event_id: Optional[str] = None
    decision_source: Optional[str] = None
    evidence_refs: List[Dict[str, Any]] = Field(default_factory=list)
    outcome_status: Optional[str] = "pending"
    outcome_note: Optional[str] = ""
    outcome_recorded_at: Optional[str] = ""
    outcome_recorded_by: Optional[str] = ""


class NursingDecisionResponse(_CodeMessage):
    patient_id: str
    symptom: str
    retrieved_context: str
    llm_advice: str
    decision_id: Optional[str] = None
    evidence: List[EvidenceItem] = Field(default_factory=list)
    memory: List[DecisionMemoryItem] = Field(default_factory=list)


# ============================================================
# 症状提示词优化
# ============================================================
class PromptOptimizeRequest(BaseModel):
    """POST /api/nursing/optimize_prompt"""

    patient_id: str = Field(..., min_length=1)
    raw_symptom: str = Field(..., min_length=1)


class PromptOptimizeResponse(_CodeMessage):
    patient_id: str
    original_symptom: str
    optimized_symptom: str
    retrieved_context: str


# ============================================================
# 决策记忆 outcome 回填（L4 闭环）
# ============================================================
OutcomeStatus = Literal["pending", "effective", "ineffective", "partial"]


class OutcomeRecordRequest(BaseModel):
    """PATCH /api/nursing/decisions/{decision_id}/outcome"""

    outcome_status: OutcomeStatus
    note: Optional[str] = None
    recorded_by: Optional[str] = None


# ============================================================
# AI 护理任务卡 / 事件闭环
# ============================================================
class TaskCardGenerateRequest(BaseModel):
    """POST /api/nursing/task-card"""

    patient_id: str = Field(..., min_length=1)
    symptom: str = Field(..., min_length=1)
    reporter: Optional[str] = None
    location: Optional[str] = None
    n_results: Optional[int] = Field(default=None, ge=1, le=20)


class TaskCompleteRequest(BaseModel):
    """PATCH /api/nursing/events/{event_id}/tasks/{task_id}/complete"""

    # 路由里校验 done / abnormal / skipped，且有 `payload.status or "done"`
    status: Optional[Literal["done", "abnormal", "skipped"]] = "done"
    completed_by: Optional[str] = None
    note: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None


class EventObservationRequest(BaseModel):
    """POST /api/nursing/events/{event_id}/observations"""

    vital_type: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)
    unit: Optional[str] = None
    note: Optional[str] = None
    recorded_by: Optional[str] = None


__all__ = [
    # EHR
    "EHRAddRequest",
    "EHRAddResponse",
    "EHRUpdateRequest",
    "EHRUpdateResponse",
    "EHRDeleteRequest",
    "EHRDeleteResponse",
    "EHRRecord",
    "EHRListResponse",
    # Decision
    "NursingDecisionRequest",
    "NursingDecisionResponse",
    "EvidenceItem",
    "DecisionMemoryItem",
    # Optimize
    "PromptOptimizeRequest",
    "PromptOptimizeResponse",
    # Outcome
    "OutcomeRecordRequest",
    # Task card / events
    "TaskCardGenerateRequest",
    "TaskCompleteRequest",
    "EventObservationRequest",
]
