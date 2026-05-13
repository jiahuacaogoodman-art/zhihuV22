# -*- coding: utf-8 -*-
"""
@File    : routers/nursing.py
@Desc    : 护理决策支持路由：RAG 推理（普通 + 流式 SSE）、提示词优化、患者信息查询
"""

import json
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from loguru import logger

from app.models.schemas import (
    NursingDecisionRequest, NursingDecisionResponse,
    PromptOptimizeRequest, PromptOptimizeResponse,
    EvidenceItem, DecisionMemoryItem, OutcomeRecordRequest,
)
from app.middleware.auth import get_current_user
from app.services.audit_log import get_audit_log
from app.services.llm_service import get_llm_service
from app.services.pii_crypto import decrypt_pii_fields
from app.services.retrieval import HybridRetriever, format_evidence_block, legacy_context_string
from app.services.decision_memory import DecisionMemory, format_memory_block
from app.services.user_store import User
from app.core.config import RAG_PROMPT_TEMPLATE, OLLAMA_MODEL_NAME

router = APIRouter()
# 工厂根据 LLM_PROVIDER（ollama / openai）选 provider，路由层零感知。
llm_service = get_llm_service()
# 与 ehr 路由共用同一个审计实例（全局 singleton）
audit = get_audit_log()

OPTIMIZE_PROMPT_TEMPLATE = """你是一名专业的医疗文书助手。
患者既往病史及用药记录：{context}

护工原始描述（口语化）：{raw_symptom}

请根据患者的既往病史，将护工的口语化描述改写为一段更规范、更专业的症状描述，
突出与病史相关的高风险信号，方便医生快速判断。
只输出改写后的症状描述，不要添加任何解释或前缀。"""


AI_TASK_CARD_PROMPT_TEMPLATE = """你是一个养老机构护理安全辅助系统中的“AI任务卡生成器”。

你的任务：基于【老人EHR档案】和【护工口语描述】，直接生成一张可执行、可打卡、可入档的护理任务卡。

重要边界：
1. 你只能做护理辅助、风险分诊、流程提醒，不能做确诊，不能开处方，不能让普通护工自行给药。
2. 任务必须具体、短句、可执行、可打卡，不能只写“建议观察”“建议就医”这种空话。
3. 必须体现个体化：把EHR中的病史、用药、过敏、护理等级等作为依据。
4. 输出必须是严格 JSON，不要 Markdown，不要代码块，不要解释。
5. 如果症状很危险，应升级 risk_level；如果信息不足，要在任务里要求补充测量和上报，而不是臆断。

【老人EHR档案】
{retrieved_context}

【护工口语描述】
{symptom}

【当前时间】
{now}

请严格按以下 JSON 结构输出：
{{
  "event_type": "事件类型，例如：疑似低血糖风险事件/跌倒外伤风险事件/胸闷气短高危事件/一般不适观察事件",
  "risk_level": "red/orange/yellow/green 四选一",
  "summary": "一句话说明为什么这样分级，必须提到症状和个体化档案依据",
  "evidence": [
    {{"source": "护工症状描述", "content": "从描述中抽取的关键表现"}},
    {{"source": "老人EHR/病历档案", "content": "从EHR中抽取的个体化高危因素"}},
    {{"source": "护理安全逻辑", "content": "为什么需要这些处置任务"}}
  ],
  "nursing_advice": {{
    "title": "护理建议标题",
    "summary": "面向护工的总体护理建议",
    "focus_points": ["护理重点1", "护理重点2", "护理重点3"],
    "handover_hint": "交接提醒",
    "safety_boundary": "安全边界提醒"
  }},
  "immediate_tasks": [
    {{
      "text": "可执行任务，20字以内优先",
      "priority": "high/medium/low",
      "required": true,
      "input_required": {{"field": "字段英文名", "label": "填写项中文名", "unit": "单位"}}
    }}
  ],
  "conditional_tasks": [
    {{"condition": "如果/若……", "tasks": ["条件任务1", "条件任务2"]}}
  ],
  "forbidden_actions": ["禁止事项1", "禁止事项2"],
  "recheck_plan": [
    {{"after_minutes": 15, "text": "复测/观察内容"}}
  ],
  "handover_sbar": {{
    "S": "现状：发生了什么",
    "B": "背景：EHR中相关病史/用药/过敏",
    "A": "评估：护理风险分级与已生成任务",
    "R": "建议：交接给责任护士/负责人后的下一步"
  }}
}}

字段要求：
- immediate_tasks 至少 4 条，最多 7 条。
- 每条 immediate_tasks 必须有 text、priority、required。
- 如果任务需要填写数值，例如血糖、体温、血压、血氧，请提供 input_required；不需要填写数值时 input_required 可以省略。
- forbidden_actions 至少 3 条。
- recheck_plan 至少 1 条。
- risk_level 必须是 red/orange/yellow/green，不能输出中文。
"""


def _get_state():
    from main import app_state
    collection = app_state.get("db_collection")
    embedding_function = app_state.get("embedding_function")
    if collection is None or embedding_function is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库服务未就绪，请稍后重试"
        )
    return collection, embedding_function


def _retrieve_context(collection, embedding_function, patient_id: str, query: str, n_results: int = 3) -> str:
    """旧接口：只回字符串。保留给 prompt 优化等非关键路径用。"""
    retriever = HybridRetriever(collection, embedding_function)
    evidence = retriever.retrieve(patient_id=patient_id, query=query, top_k=max(n_results, 3))
    if not evidence:
        return f"（未检索到 patient_id='{patient_id}' 的相关档案，请先录入档案）"
    return legacy_context_string(evidence)


def _retrieve_evidence(
    collection,
    embedding_function,
    patient_id: str,
    query: str,
    n_results: int = 5,
):
    """新接口：返回 (evidence_list, memory_list, evidence_block_text, memory_block_text)。
    - evidence_list 含 decision_log（过往决策）类证据，Prompt 能直接引用 [E1]..[En]
    - memory_list 单独拿出最近 3 条决策，生成"决策回忆"提示块
    """
    retriever = HybridRetriever(collection, embedding_function)
    # 主证据：各来源都检索，让 decision_log 也能自然被召回
    evidence = retriever.retrieve(patient_id=patient_id, query=query, top_k=max(n_results, 3))

    # 近期决策记忆（时间优先，不依赖语义召回）
    memory = DecisionMemory(collection, embedding_function).list_decisions(
        patient_id=patient_id, limit=3, days=7
    )

    evidence_block = format_evidence_block(evidence)
    memory_block = format_memory_block(memory)
    return evidence, memory, evidence_block, memory_block


# ── 引用感知的 Prompt 模板 ────────────────────────────────────
CITATION_RAG_PROMPT = """你是经验丰富的养老护理辅助 AI。请严格基于下列证据生成个性化护理建议。

【证据清单】（请在回答中以 [E1]、[E2] 等形式显式引用具体证据，禁止编造）
{evidence_block}

{memory_block_section}

【当前事件】
患者编号：{patient_id}
症状描述：{symptom}

【回答要求】
1. 回答必须引用证据编号，如「该患者既往有糖尿病病史 [E1]」「结合 3 天前类似主诉当时建议复测血糖 [E3]」。
2. 若证据不足以支持判断，明确写"证据不足"，不要臆测。
3. 若"决策回忆"中存在与当前类似的已执行事件，开头用一句话连接："结合该患者 N 天前类似事件（当时建议 X，执行结果 Y），本次判断..."。
4. 分步骤给出可执行建议，涉及给药/治疗一律提示交由责任护士/医生处理。
5. 最后一行标明"何时必须立即升级上报"。
"""


# ── 查询单个患者信息（护工端用）─────────────────────────────────
@router.get(
    "/nursing/patient/{patient_id}",
    summary="查询患者档案摘要（护工端使用）"
)
async def get_patient_info(patient_id: str, user: User = Depends(get_current_user)):
    """根据 patient_id 返回患者的完整档案信息，供护工端预览。

    注意：ChromaDB metadata 里的 PII 字段（name / bed_number / allergy /
    emergency_* / notes 等）在写入时被 Fernet 加密（enc: 前缀），这里必须
    先调 decrypt_pii_fields，否则护工端页面会看到一串密文乱码。
    """
    collection, _ = _get_state()
    try:
        result = collection.get(
            where={"patient_id": {"$eq": patient_id}},
            include=["documents", "metadatas"]
        )
        ids = result.get("ids", [])
        if not ids:
            raise HTTPException(status_code=404, detail=f"未找到患者 '{patient_id}' 的档案")
        profile_idx = 0
        for i, m in enumerate(result.get("metadatas", [])):
            if m.get("doc_type") in (None, "", "patient_profile"):
                profile_idx = i
                break
        # 透明解密 PII 字段；未加密部署下为 no-op
        meta = decrypt_pii_fields(result["metadatas"][profile_idx])
        doc = result["documents"][profile_idx]
        response_body = {
            "code": 200,
            "patient_id": patient_id,
            "name": meta.get("name", ""),
            "age": meta.get("age"),
            "gender": meta.get("gender"),
            "care_level": meta.get("care_level"),
            "bed_number": meta.get("bed_number"),
            "primary_nurse": meta.get("primary_nurse"),
            "allergy": meta.get("allergy"),
            "diet_restriction": meta.get("diet_restriction"),
            "blood_type": meta.get("blood_type"),
            "emergency_contact": meta.get("emergency_contact"),
            "emergency_phone": meta.get("emergency_phone"),
            "emergency_relation": meta.get("emergency_relation"),
            "medical_history": doc,
            "notes": meta.get("notes"),
        }
        # 只读审计：护工端查看患者档案；与 ehr 入口区分，便于追溯访问来源
        audit.log("PATIENT_READ", patient_id, user.username,
                  detail=f"查看患者档案: {meta.get('name', '')} (来源=nursing)")
        return response_body
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


# ── RAG 普通推理（保留兼容）──────────────────────────────────────
@router.post(
    "/nursing/decision",
    response_model=NursingDecisionResponse,
    summary="RAG 联合推理（混合检索 + 引用 + 自动写入决策记忆）"
)
async def nursing_decision(payload: NursingDecisionRequest):
    collection, embedding_function = _get_state()
    logger.info(f"护理决策（普通）: patient_id={payload.patient_id}")

    evidence, memory, evidence_block, memory_block = _retrieve_evidence(
        collection, embedding_function,
        payload.patient_id, payload.symptom, n_results=payload.n_results or 5
    )
    memory_block_section = f"\n【决策回忆】\n{memory_block}\n" if memory_block else ""
    final_prompt = CITATION_RAG_PROMPT.format(
        evidence_block=evidence_block,
        memory_block_section=memory_block_section,
        patient_id=payload.patient_id,
        symptom=payload.symptom,
    )
    try:
        llm_advice = llm_service.generate(
            prompt=final_prompt,
            options={"temperature": 0.3, "top_p": 0.9, "num_predict": 1024}
        )
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except (TimeoutError, ValueError) as e:
        raise HTTPException(status_code=500, detail=str(e))

    # 写入决策记忆（L4 闭环）
    patient_name = _find_patient_name_from_collection(collection, payload.patient_id)
    memory_service = DecisionMemory(collection, embedding_function)
    log_result = memory_service.log_decision(
        patient_id=payload.patient_id,
        symptom=payload.symptom,
        advice=llm_advice,
        evidence=[e.to_dict() for e in evidence],
        patient_name=patient_name,
        decision_source="nursing_decision",
    )

    return NursingDecisionResponse(
        code=200,
        patient_id=payload.patient_id,
        symptom=payload.symptom,
        retrieved_context=legacy_context_string(evidence),
        llm_advice=llm_advice,
        decision_id=log_result.get("decision_id"),
        evidence=[EvidenceItem(**e.to_dict()) for e in evidence],
        memory=[DecisionMemoryItem(**m) for m in memory],
    )


def _find_patient_name_from_collection(collection, patient_id: str) -> str:
    try:
        result = collection.get(
            where={"patient_id": {"$eq": patient_id}}, include=["metadatas"]
        )
        for meta in result.get("metadatas", []) or []:
            if meta and meta.get("doc_type") in (None, "", "patient_profile") and meta.get("name"):
                return meta["name"]
        for meta in result.get("metadatas", []) or []:
            if meta and meta.get("name"):
                return meta["name"]
    except Exception:
        pass
    return ""


# ── RAG 流式推理（SSE）──────────────────────────────────────────
@router.post(
    "/nursing/decision/stream",
    summary="RAG 联合推理（流式 SSE + 证据 + 决策记忆）"
)
async def nursing_decision_stream(payload: NursingDecisionRequest):
    """
    SSE 事件：
    - event: context   → 检索到的病史文本（兼容老前端）
    - event: evidence  → 结构化证据列表 + 决策记忆
    - event: token     → 生成的 token 片段
    - event: done      → [DONE] + decision_id
    - event: error     → 错误信息
    """
    collection, embedding_function = _get_state()
    logger.info(f"护理决策（流式）: patient_id={payload.patient_id}")

    try:
        evidence, memory, evidence_block, memory_block = _retrieve_evidence(
            collection, embedding_function,
            payload.patient_id, payload.symptom, n_results=payload.n_results or 5
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"混合检索失败: {str(e)}")

    memory_block_section = f"\n【决策回忆】\n{memory_block}\n" if memory_block else ""
    final_prompt = CITATION_RAG_PROMPT.format(
        evidence_block=evidence_block,
        memory_block_section=memory_block_section,
        patient_id=payload.patient_id,
        symptom=payload.symptom,
    )
    patient_name = _find_patient_name_from_collection(collection, payload.patient_id)

    def event_generator():
        # 1. 先推检索上下文（兼容老前端）
        context_data = json.dumps({
            "patient_id": payload.patient_id,
            "symptom": payload.symptom,
            "retrieved_context": legacy_context_string(evidence)
        }, ensure_ascii=False)
        yield f"event: context\ndata: {context_data}\n\n"

        # 2. 推结构化证据 + 决策记忆（新前端用）
        evidence_data = json.dumps({
            "evidence": [e.to_dict() for e in evidence],
            "memory": memory,
        }, ensure_ascii=False)
        yield f"event: evidence\ndata: {evidence_data}\n\n"

        # 3. 流式推 token，同时拼接完整 advice 供 L4 决策记忆写入
        collected = []
        try:
            for token in llm_service.generate_stream(
                prompt=final_prompt,
                options={"temperature": 0.3, "top_p": 0.9, "num_predict": 1024}
            ):
                collected.append(token)
                yield f"event: token\ndata: {json.dumps(token, ensure_ascii=False)}\n\n"
        except ConnectionError as e:
            yield f"event: error\ndata: {json.dumps(str(e), ensure_ascii=False)}\n\n"
            return
        except Exception as e:
            yield f"event: error\ndata: {json.dumps(str(e), ensure_ascii=False)}\n\n"
            return

        # 4. 写决策记忆
        full_advice = "".join(collected)
        try:
            log_result = DecisionMemory(collection, embedding_function).log_decision(
                patient_id=payload.patient_id,
                symptom=payload.symptom,
                advice=full_advice,
                evidence=[e.to_dict() for e in evidence],
                patient_name=patient_name,
                decision_source="nursing_decision_stream",
            )
        except Exception as e:
            logger.warning(f"决策记忆写入失败: {e}")
            log_result = {"decision_id": None}

        # 5. done（带 decision_id 给前端挂结果记录）
        done_data = json.dumps({"decision_id": log_result.get("decision_id")}, ensure_ascii=False)
        yield f"event: done\ndata: {done_data}\n\n"
        # 兼容老前端：再推一个裸 [DONE]
        yield "event: done\ndata: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


# ── 决策记忆：列表 + outcome 回填 ─────────────────────────────
@router.get(
    "/nursing/decisions",
    summary="查询该患者的 AI 决策记忆（L4 闭环）"
)
async def list_decision_memory(
    patient_id: str,
    limit: int = 20,
    days: int = 30,
    user: User = Depends(get_current_user),
):
    collection, embedding_function = _get_state()
    memory = DecisionMemory(collection, embedding_function)
    records = memory.list_decisions(patient_id=patient_id, limit=limit, days=days)
    # 只读审计：查询决策记忆列表
    audit.log("DECISION_READ", patient_id, user.username,
              detail=f"查询决策记忆列表，limit={limit} days={days} returned={len(records)}")
    return {"code": 200, "patient_id": patient_id, "total": len(records), "decisions": records}


@router.get(
    "/nursing/decisions/{decision_id}",
    summary="查询单条决策记忆"
)
async def get_decision_memory(decision_id: str, user: User = Depends(get_current_user)):
    collection, embedding_function = _get_state()
    memory = DecisionMemory(collection, embedding_function)
    record = memory.get_decision(decision_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"未找到 decision_id={decision_id}")
    # 只读审计：成功命中才记录；patient_id 从记录里拿，拿不到就空串
    audit.log("DECISION_READ",
              record.get("patient_id", "") if isinstance(record, dict) else "",
              user.username,
              doc_id=decision_id,
              detail=f"查询单条决策记忆: {decision_id}")
    return {"code": 200, "decision": record}


@router.patch(
    "/nursing/decisions/{decision_id}/outcome",
    summary="回填决策执行结果（L4 闭环：effective/ineffective/partial）"
)
async def patch_decision_outcome(decision_id: str, payload: OutcomeRecordRequest):
    collection, embedding_function = _get_state()
    memory = DecisionMemory(collection, embedding_function)
    try:
        result = memory.record_outcome(
            decision_id=decision_id,
            outcome_status=payload.outcome_status,
            note=payload.note or "",
            recorded_by=payload.recorded_by or "",
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"code": 200, "message": "决策执行结果已记录，下一次检索时 AI 将看到此结果", **result}


# ── 提示词自动优化（基于病史）────────────────────────────────────
@router.post(
    "/nursing/optimize_prompt",
    response_model=PromptOptimizeResponse,
    summary="基于病史自动优化症状提示词"
)
async def optimize_prompt(payload: PromptOptimizeRequest):
    collection, embedding_function = _get_state()
    logger.info(f"提示词优化: patient_id={payload.patient_id}")

    try:
        context = _retrieve_context(
            collection, embedding_function,
            payload.patient_id, payload.raw_symptom, n_results=3
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"病史检索失败: {str(e)}")

    optimize_prompt_text = OPTIMIZE_PROMPT_TEMPLATE.format(
        context=context,
        raw_symptom=payload.raw_symptom
    )
    try:
        optimized = llm_service.generate(
            prompt=optimize_prompt_text,
            options={"temperature": 0.2, "num_predict": 256}
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"大模型调用失败: {str(e)}")

    return PromptOptimizeResponse(
        code=200,
        patient_id=payload.patient_id,
        original_symptom=payload.raw_symptom,
        optimized_symptom=optimized.strip(),
        retrieved_context=context
    )


# ============================================================
# v17 新增：AI 护理任务卡 / 突发事件处置闭环
# 设计目标：把 AI 建议转化为可执行、可打卡、可复测、可交接的护理流程单。
# ============================================================

import re
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.config import BASE_DIR
from app.models.schemas import TaskCardGenerateRequest, TaskCompleteRequest, EventObservationRequest
from app.services.event_store import EventStore
from app.services.protocol_loader import get_protocols

# ── 护理事件持久化（SQLite，替代原 events.json 全量覆写方案）──────────────
# EventStore 在模块加载时初始化一次；首次启动会自动把旧 events.json 迁移过来。
_EVENT_STORE_DIR = Path(BASE_DIR) / "local_nursing_events"
_EVENT_STORE_DIR.mkdir(parents=True, exist_ok=True)
_event_store = EventStore(
    db_path=_EVENT_STORE_DIR / "events.db",
    legacy_json=_EVENT_STORE_DIR / "events.json",   # 存在则自动迁移后重命名为 .bak
)

RISK_META = {
    "red": {"label": "红色预警", "title": "高危事件", "color": "#ef4444"},
    "orange": {"label": "橙色预警", "title": "中高危事件", "color": "#f97316"},
    "yellow": {"label": "黄色提醒", "title": "需观察事件", "color": "#f59e0b"},
    "green": {"label": "绿色观察", "title": "常规观察", "color": "#10b981"},
}

# 护理协议模板已迁移至 data/protocols.yaml，由 get_protocols() 热加载。
# 如需新增或修改协议，请编辑 data/protocols.yaml 文件，无需重启服务。

def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_event_or_404(event_id: str) -> dict:
    event = _event_store.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="未找到该护理事件")
    return event


def _update_event(event_id: str, updater) -> dict:
    try:
        return _event_store.update_event(event_id, updater)
    except KeyError:
        raise HTTPException(status_code=404, detail="未找到该护理事件")


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _hit_any(text: str, words: list[str]) -> list[str]:
    raw = text or ""
    return [w for w in words if w.lower() in raw.lower()]


def _classify_protocol(symptom: str, context: str) -> tuple[str, dict, list[str]]:
    """根据护工描述 + EHR 上下文匹配事件模板。"""
    combined = f"{symptom}\n{context}"
    protocols = get_protocols()
    if not protocols:
        return "general", _general_protocol(), []
    scores = []
    for key, p in protocols.items():
        symptom_hits = _hit_any(symptom, p["triggers"])
        ehr_hits = _hit_any(context, p["high_risk_ehr"])
        score = len(symptom_hits) * 3 + len(ehr_hits)
        # 意识异常、胸闷等高危词优先。
        if key in ("chest_distress", "consciousness") and symptom_hits:
            score += 2
        scores.append((score, key, symptom_hits + ehr_hits))
    scores.sort(reverse=True, key=lambda x: x[0])
    best_score, best_key, hits = scores[0]
    if best_score <= 0:
        # 未命中时使用“发热/常规观察”不合适，给一个通用观察模板。
        return "general", _general_protocol(), []
    return best_key, protocols[best_key], hits


def _general_protocol() -> dict:
    return {
        "event_type": "一般不适/待进一步观察事件",
        "risk_default": "yellow",
        "triggers": [],
        "high_risk_ehr": [],
        "immediate_tasks": [
            {"text": "确认老人身份、床号和当前主诉", "priority": "high", "required": True},
            {"text": "观察意识、呼吸、面色、疼痛和活动情况", "priority": "high", "required": True},
            {"text": "测量可获得的生命体征", "priority": "medium", "required": True},
            {"text": "通知责任护士或值班负责人", "priority": "medium", "required": True},
            {"text": "记录症状开始时间、持续时间和诱因", "priority": "medium", "required": True},
        ],
        "conditional_tasks": [{"condition": "若症状加重、意识改变、呼吸困难或持续不缓解", "tasks": ["立即升级风险等级", "联系负责人启动进一步处置", "准备病历和用药清单"]}],
        "forbidden_actions": ["不要自行诊断", "不要擅自给药", "不要让老人独处", "不要漏记观察变化"],
        "recheck_plan": [{"after_minutes": 15, "text": "复查症状和生命体征"}],
    }


def _risk_level(protocol_key: str, protocol: dict, symptom: str, context: str) -> str:
    risk = protocol.get("risk_default", "yellow")
    text = f"{symptom}\n{context}"
    red_flags = ["叫不醒", "意识不清", "呼吸困难", "胸痛", "胸闷", "嘴唇发紫", "抽搐", "一侧无力", "说话不清", "大汗", "撞头", "不能站", "呕血", "黑便"]
    if _hit_any(text, red_flags):
        risk = "red" if protocol_key in ("chest_distress", "consciousness", "aspiration", "allergy") else "orange"
    if protocol_key == "fall" and _hit_any(text, ["华法林", "抗凝", "阿司匹林", "撞头", "不能站", "骨质疏松"]):
        risk = "red" if _hit_any(text, ["撞头", "不能站", "意识"]) else "orange"
    if protocol_key == "hypoglycemia" and _hit_any(text, ["糖尿病", "胰岛素", "意识模糊", "手抖", "大汗"]):
        risk = "red"
    if protocol_key == "fever" and _hit_any(text, ["呼吸困难", "意识", "寒战", "糖尿病", "慢阻肺"]):
        risk = "orange"
    return risk


def _extract_profile_from_context(patient_id: str, context: str) -> dict:
    name = ""
    bed = ""
    age = None
    try:
        # 优先从 EHR REST 逻辑拿完整档案，失败时用上下文正则兜底。
        # 避免循环导入 ehr 模块，这里仅做轻量文本抽取。
        m = re.search(r"患者姓名[：:](.*?)[，,；]", context or "")
        name = m.group(1).strip() if m else ""
        b = re.search(r"床位号[：:](.*?)[；，,\n]", context or "")
        bed = b.group(1).strip() if b else ""
        a = re.search(r"年龄[：:](\d+)", context or "")
        age = int(a.group(1)) if a else None
    except Exception:
        pass
    return {"patient_id": patient_id, "name": name, "bed_number": bed, "age": age}


def _task_items(items: list[dict]) -> list[dict]:
    out = []
    for idx, item in enumerate(items, start=1):
        task = deepcopy(item)
        task.update({
            "task_id": f"t{idx}",
            "status": "pending",
            "completed_at": None,
            "completed_by": None,
            "note": None,
            "value": None,
            "unit": task.get("input_required", {}).get("unit") if task.get("input_required") else None,
            "audit_trail": [],
        })
        out.append(task)
    return out


def _event_log(title: str, detail: str, operator: str | None = None) -> dict:
    return {
        "log_id": f"log_{uuid.uuid4().hex[:8]}",
        "title": title,
        "detail": detail,
        "operator": operator or "护工端",
        "created_at": _now_str(),
    }


def _task_progress(event: dict) -> dict:
    tasks = event.get("immediate_tasks", [])
    total = len(tasks)
    handled = len([t for t in tasks if t.get("status") in ("done", "abnormal", "skipped")])
    required_total = len([t for t in tasks if t.get("required")])
    required_done = len([t for t in tasks if t.get("required") and t.get("status") in ("done", "abnormal", "skipped")])
    return {
        "total": total,
        "handled": handled,
        "percent": round(handled / total * 100) if total else 0,
        "required_total": required_total,
        "required_done": required_done,
    }


def _build_nursing_advice(protocol: dict, risk: str, context: str) -> dict:
    """生成与任务卡同时返回的护理建议，避免前端再单独请求普通 AI。"""
    event_type = protocol.get("event_type", "护理观察事件")
    risk_label = RISK_META.get(risk, RISK_META["yellow"])["label"]
    focus = []
    if risk == "red":
        focus.append("当前为红色预警，应优先保护老人安全并立即通知责任护士/值班负责人。")
    elif risk == "orange":
        focus.append("当前为橙色预警，应尽快完成关键观察、测量与上报。")
    else:
        focus.append("当前需重点观察，按任务卡完成记录并按时复测。")
    focus.extend([
        "先执行可操作护理动作，再记录测量值与观察结果。",
        "涉及给药、诊断或治疗决策时，普通护工不得自行处理，必须按机构流程转交责任护士/医生。",
        "任务执行状态、备注、复测结果会自动进入事件档案，供交接和追溯使用。",
    ])
    return {
        "title": f"{risk_label} · {event_type}护理建议",
        "summary": f"系统已根据老人档案、当前症状与护理流程模板生成任务卡；请逐项打卡执行，并在完成后入档。",
        "focus_points": focus,
        "handover_hint": "建议处理后使用系统自动生成的 SBAR 交接单，交由责任护士/值班负责人复核。",
        "safety_boundary": "本建议仅用于护理辅助、风险分诊和流程提醒，不替代医生诊断，不构成处方或治疗医嘱。",
    }


def _build_sbar(patient_name: str, symptom: str, protocol: dict, risk: str, context: str) -> dict:
    risk_label = RISK_META.get(risk, RISK_META["yellow"])["label"]
    short_context = (context or "").replace("\n", "；")[:180]
    return {
        "S": f"{patient_name or '该老人'}出现：{symptom}。系统标记为{risk_label}。",
        "B": f"已调取老人EHR与病历片段：{short_context or '暂无可用档案'}。",
        "A": f"根据症状与个人档案，当前按“{protocol.get('event_type')}”进行护理风险分诊，已生成任务卡供护工逐项执行。",
        "R": "建议责任护士/值班负责人复核任务执行情况；若出现红旗征象或症状持续加重，应按机构流程联系医生或120。",
    }



def _extract_json_from_llm(text: str) -> dict:
    """从大模型输出中稳健提取 JSON 对象。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.I).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidate = raw[start:end + 1]
        return json.loads(candidate)
    raise ValueError("大模型未返回可解析的 JSON 对象")


def _normalize_risk_level(value: str) -> str:
    value = (value or "").strip().lower()
    mapping = {
        "红": "red", "红色": "red", "红色预警": "red", "high": "red", "高": "red", "高危": "red",
        "橙": "orange", "橙色": "orange", "橙色预警": "orange", "medium_high": "orange", "中高": "orange",
        "黄": "yellow", "黄色": "yellow", "黄色提醒": "yellow", "medium": "yellow", "中": "yellow",
        "绿": "green", "绿色": "green", "绿色观察": "green", "low": "green", "低": "green",
    }
    return value if value in RISK_META else mapping.get(value, "yellow")


def _sanitize_task(task: dict, idx: int) -> dict:
    """把 AI 任务归一化为前端可打卡的数据结构。"""
    if not isinstance(task, dict):
        task = {"text": str(task)}
    text = str(task.get("text") or f"护理观察任务{idx}").strip()
    priority = str(task.get("priority") or "medium").lower()
    if priority not in {"high", "medium", "low"}:
        priority = "medium"
    required = task.get("required", True)
    input_required = task.get("input_required")
    if not isinstance(input_required, dict):
        input_required = None
    item = {
        "text": text,
        "priority": priority,
        "required": bool(required),
        "status": "pending",
        "completed_at": None,
        "completed_by": None,
        "note": None,
        "value": None,
        "unit": input_required.get("unit") if input_required else None,
        "audit_trail": [],
    }
    if input_required:
        item["input_required"] = {
            "field": str(input_required.get("field") or f"field_{idx}"),
            "label": str(input_required.get("label") or "数值/观察结果"),
            "unit": str(input_required.get("unit") or ""),
        }
    return item


def _sanitize_conditional_tasks(items) -> list[dict]:
    out = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        tasks = item.get("tasks") or []
        if isinstance(tasks, str):
            tasks = [tasks]
        out.append({
            "condition": str(item.get("condition") or "若情况变化"),
            "tasks": [str(x) for x in tasks][:6],
        })
    return out


def _normalize_ai_card(ai_data: dict, payload: TaskCardGenerateRequest, context: str, raw_llm: str) -> dict:
    """把大模型 JSON 统一为 v18 前端/入档接口兼容的 task_card。"""
    if not isinstance(ai_data, dict):
        raise ValueError("AI 任务卡不是 JSON 对象")

    event_id = f"evt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    profile = _extract_profile_from_context(payload.patient_id, context)
    patient_name = profile.get("name") or payload.patient_id
    risk = _normalize_risk_level(ai_data.get("risk_level"))

    tasks_raw = ai_data.get("immediate_tasks") or []
    if not isinstance(tasks_raw, list):
        tasks_raw = []
    if len(tasks_raw) < 1:
        raise ValueError("AI 未生成 immediate_tasks")
    tasks = []
    for idx, task in enumerate(tasks_raw[:7], start=1):
        normalized = _sanitize_task(task, idx)
        normalized["task_id"] = f"t{idx}"
        tasks.append(normalized)

    evidence_raw = ai_data.get("evidence") or []
    evidence = []
    if isinstance(evidence_raw, list):
        for e in evidence_raw[:8]:
            if isinstance(e, dict):
                evidence.append({"source": str(e.get("source") or "AI依据"), "content": str(e.get("content") or "")})
            else:
                evidence.append({"source": "AI依据", "content": str(e)})
    if context and "未检索到" not in context:
        evidence.append({"source": "RAG检索", "content": "已调取该老人EHR/病历片段参与任务卡生成"})

    advice = ai_data.get("nursing_advice") or {}
    if not isinstance(advice, dict):
        advice = {"summary": str(advice)}
    focus_points = advice.get("focus_points") or []
    if isinstance(focus_points, str):
        focus_points = [focus_points]
    nursing_advice = {
        "title": str(advice.get("title") or f"{RISK_META.get(risk, RISK_META['yellow'])['label']}护理建议"),
        "summary": str(advice.get("summary") or ai_data.get("summary") or "请按任务卡逐项执行并及时交接。"),
        "focus_points": [str(x) for x in focus_points][:8],
        "handover_hint": str(advice.get("handover_hint") or "处理后请生成SBAR交接单，交由责任护士/值班负责人复核。"),
        "safety_boundary": str(advice.get("safety_boundary") or "本建议仅用于护理辅助和风险分诊，不替代医生诊断，不构成处方或治疗医嘱。"),
    }

    forbidden = ai_data.get("forbidden_actions") or []
    if isinstance(forbidden, str):
        forbidden = [forbidden]
    forbidden = [str(x) for x in forbidden][:8]

    recheck_raw = ai_data.get("recheck_plan") or []
    recheck = []
    if isinstance(recheck_raw, list):
        for r in recheck_raw[:6]:
            if not isinstance(r, dict):
                continue
            try:
                minutes = int(r.get("after_minutes") or 15)
            except Exception:
                minutes = 15
            recheck.append({"after_minutes": max(1, minutes), "text": str(r.get("text") or "复查症状变化")})
    if not recheck:
        recheck = [{"after_minutes": 15, "text": "复查症状与生命体征"}]

    sbar = ai_data.get("handover_sbar") or {}
    if not isinstance(sbar, dict):
        sbar = {}
    handover_sbar = {
        "S": str(sbar.get("S") or f"{patient_name}出现：{payload.symptom}。"),
        "B": str(sbar.get("B") or f"已调取老人EHR：{(context or '')[:160]}"),
        "A": str(sbar.get("A") or f"AI生成任务卡，风险等级为{RISK_META.get(risk, RISK_META['yellow'])['label']}。"),
        "R": str(sbar.get("R") or "建议责任护士/值班负责人复核任务执行情况，必要时联系医生。"),
    }

    event_type = str(ai_data.get("event_type") or "AI生成护理任务卡事件")
    task_card = {
        "event_id": event_id,
        "generation_mode": "ai_llm",
        "llm_model": OLLAMA_MODEL_NAME,
        "patient_id": payload.patient_id,
        "patient_name": patient_name,
        "bed_number": profile.get("bed_number") or payload.location or "—",
        "age": profile.get("age"),
        "event_type": event_type,
        "raw_description": payload.symptom,
        "risk_level": risk,
        "risk_label": RISK_META.get(risk, RISK_META["yellow"])["label"],
        "status": "processing",
        "reporter": payload.reporter or "护工端上报",
        "location": payload.location,
        "created_at": _now_str(),
        "summary": str(ai_data.get("summary") or f"AI结合老人档案和症状生成了{event_type}任务卡。"),
        "nursing_advice": nursing_advice,
        "evidence": evidence,
        "retrieved_context": context,
        "immediate_tasks": tasks,
        "conditional_tasks": _sanitize_conditional_tasks(ai_data.get("conditional_tasks")),
        "forbidden_actions": forbidden,
        "recheck_plan": recheck,
        "observations": [],
        "execution_logs": [_event_log("AI任务卡生成", f"由本地大模型 {OLLAMA_MODEL_NAME} 生成护理建议、任务卡、复测计划和SBAR草稿", payload.reporter or "护工端")],
        "progress": {"total": len(tasks), "handled": 0, "percent": 0, "required_total": len([t for t in tasks if t.get("required")]), "required_done": 0},
        "handover_sbar": handover_sbar,
        "safety_boundary": nursing_advice["safety_boundary"],
        "llm_raw_response": raw_llm[:4000],
    }
    return task_card


def _build_ai_task_card(payload: TaskCardGenerateRequest, context: str) -> dict:
    """真正调用本地大模型生成任务卡。关键词模板不参与默认任务卡生成。"""
    prompt = AI_TASK_CARD_PROMPT_TEMPLATE.format(
        retrieved_context=context,
        symptom=payload.symptom,
        now=_now_str(),
    )
    try:
        raw = llm_service.generate(
            prompt=prompt,
            options={
                "temperature": 0.15,
                "top_p": 0.85,
                "num_predict": 1600,
                "format": "json",
            },
        )
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"本地大模型不可用，无法生成AI任务卡：{str(e)}")
    except (TimeoutError, ValueError) as e:
        raise HTTPException(status_code=502, detail=f"本地大模型生成失败：{str(e)}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"本地大模型调用异常：{str(e)}")

    try:
        ai_data = _extract_json_from_llm(raw)
        return _normalize_ai_card(ai_data, payload, context, raw)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI任务卡JSON解析/校验失败：{str(e)}；模型原始输出前500字：{raw[:500]}")

def _build_task_card(payload: TaskCardGenerateRequest, context: str) -> dict:
    protocol_key, protocol, hits = _classify_protocol(payload.symptom, context)
    risk = _risk_level(protocol_key, protocol, payload.symptom, context)
    profile = _extract_profile_from_context(payload.patient_id, context)
    patient_name = profile.get("name") or payload.patient_id
    event_id = f"evt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    evidence = []
    symptom_hits = _hit_any(payload.symptom, protocol.get("triggers", []))
    ehr_hits = _hit_any(context, protocol.get("high_risk_ehr", []))
    if symptom_hits:
        evidence.append({"source": "护工症状描述", "content": "命中关键词：" + "、".join(dict.fromkeys(symptom_hits))})
    if ehr_hits:
        evidence.append({"source": "老人EHR/病历档案", "content": "命中高危因素：" + "、".join(dict.fromkeys(ehr_hits))})
    if context and "未检索到" not in context:
        evidence.append({"source": "RAG检索", "content": "已强制调取该老人相关档案片段参与分诊"})
    evidence.append({"source": "护理流程模板", "content": protocol.get("event_type", "护理观察流程")})

    task_card = {
        "event_id": event_id,
        "patient_id": payload.patient_id,
        "patient_name": patient_name,
        "bed_number": profile.get("bed_number") or payload.location or "—",
        "age": profile.get("age"),
        "event_type": protocol.get("event_type"),
        "raw_description": payload.symptom,
        "risk_level": risk,
        "risk_label": RISK_META.get(risk, RISK_META["yellow"])["label"],
        "status": "processing",
        "reporter": payload.reporter or "护工端上报",
        "location": payload.location,
        "created_at": _now_str(),
        "summary": f"结合当前描述与老人档案，系统提示：{protocol.get('event_type')}；请按任务卡逐项完成，并由责任护士/负责人复核。",
        "nursing_advice": _build_nursing_advice(protocol, risk, context),
        "evidence": evidence,
        "retrieved_context": context,
        "immediate_tasks": _task_items(protocol.get("immediate_tasks", [])),
        "conditional_tasks": protocol.get("conditional_tasks", []),
        "forbidden_actions": protocol.get("forbidden_actions", []),
        "recheck_plan": protocol.get("recheck_plan", []),
        "observations": [],
        "execution_logs": [_event_log("任务卡生成", "已同步生成护理建议、任务卡、复测计划和SBAR草稿", payload.reporter or "护工端")],
        "progress": {"total": len(protocol.get("immediate_tasks", [])), "handled": 0, "percent": 0, "required_total": len([t for t in protocol.get("immediate_tasks", []) if t.get("required")]), "required_done": 0},
        "handover_sbar": _build_sbar(patient_name, payload.symptom, protocol, risk, context),
        "safety_boundary": "本结果仅用于护理辅助、风险分诊和流程提醒，不替代医生诊断，不构成处方或治疗医嘱。",
    }
    return task_card


@router.post("/nursing/task-card", summary="调用本地大模型生成AI护理任务卡：护理建议 + 任务打卡 + 执行入档")
async def generate_task_card(payload: TaskCardGenerateRequest):
    collection, embedding_function = _get_state()
    try:
        context = _retrieve_context(
            collection,
            embedding_function,
            payload.patient_id,
            payload.symptom,
            n_results=payload.n_results or 3,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"档案检索失败: {str(e)}")

    task_card = _build_ai_task_card(payload, context)
    _event_store.save_event(task_card)

    # 任务卡也算一次 AI 决策，写入决策记忆（L4 闭环）
    try:
        patient_name = _find_patient_name_from_collection(collection, payload.patient_id) or task_card.get("patient_name")
        advice_preview = task_card.get("summary") or task_card.get("event_type") or ""
        log_result = DecisionMemory(collection, embedding_function).log_decision(
            patient_id=payload.patient_id,
            symptom=payload.symptom,
            advice=advice_preview + "\n" + json.dumps(
                {
                    "immediate_tasks": [t.get("text") for t in task_card.get("immediate_tasks", [])],
                    "forbidden_actions": task_card.get("forbidden_actions", []),
                },
                ensure_ascii=False,
            ),
            evidence=[],  # 任务卡暂未使用结构化 evidence，留作下一迭代
            patient_name=patient_name,
            event_type=task_card.get("event_type"),
            risk_level=task_card.get("risk_level"),
            event_id=task_card.get("event_id"),
            decision_source="task_card",
        )
        task_card["decision_id"] = log_result.get("decision_id")
    except Exception as e:
        logger.warning(f"任务卡决策记忆写入失败: {e}")

    logger.success(f"任务卡生成: event_id={task_card['event_id']}, patient_id={payload.patient_id}, risk={task_card['risk_level']}")
    return {"code": 200, "message": "本地大模型已生成AI护理任务卡", "task_card": task_card}


@router.get("/nursing/events", summary="查询护理事件列表")
async def list_nursing_events(patient_id: Optional[str] = None, status_filter: Optional[str] = None):
    events = _event_store.load_events(patient_id=patient_id, status_filter=status_filter)
    return {"code": 200, "total": len(events), "events": events}


@router.get("/nursing/events/{event_id}", summary="查询单个护理事件任务卡")
async def get_nursing_event(event_id: str):
    return {"code": 200, "event": _get_event_or_404(event_id)}


@router.patch("/nursing/events/{event_id}/tasks/{task_id}/complete", summary="更新任务执行状态：完成/异常/跳过，并自动入档留痕")
async def complete_care_task(event_id: str, task_id: str, payload: TaskCompleteRequest):
    allowed_status = {"done": "已完成", "abnormal": "异常上报", "skipped": "已跳过"}
    status_value = payload.status or "done"
    if status_value not in allowed_status:
        raise HTTPException(status_code=400, detail="status 只能是 done / abnormal / skipped")

    def updater(event: dict) -> dict:
        operator = payload.completed_by or event.get("reporter") or "护工端"
        for task in event.get("immediate_tasks", []):
            if task.get("task_id") == task_id:
                task["status"] = status_value
                task["completed_at"] = _now_str()
                task["completed_by"] = operator
                task["note"] = payload.note
                task["value"] = payload.value
                if payload.unit:
                    task["unit"] = payload.unit
                trail = {
                    "status": status_value,
                    "label": allowed_status[status_value],
                    "operator": operator,
                    "value": payload.value,
                    "unit": payload.unit or task.get("unit"),
                    "note": payload.note,
                    "created_at": _now_str(),
                }
                task.setdefault("audit_trail", []).append(trail)
                detail = f"{task.get('text')}｜状态：{allowed_status[status_value]}"
                if payload.value:
                    detail += f"｜记录值：{payload.value}{payload.unit or task.get('unit') or ''}"
                if payload.note:
                    detail += f"｜备注：{payload.note}"
                event.setdefault("execution_logs", []).append(_event_log("任务执行", detail, operator))
                break
        else:
            raise HTTPException(status_code=404, detail="未找到该任务")

        event["progress"] = _task_progress(event)
        if status_value == "abnormal":
            event["status"] = "escalated"
            event.setdefault("execution_logs", []).append(_event_log("异常升级", "护工在任务执行中标记异常，建议责任护士/值班负责人复核", operator))
        else:
            event["status"] = "processing"
        event["updated_at"] = _now_str()
        return event

    event = _update_event(event_id, updater)
    return {"code": 200, "message": f"任务状态已更新：{allowed_status[status_value]}", "event": event}


@router.post("/nursing/events/{event_id}/observations", summary="追加复测/观察记录")
async def add_event_observation(event_id: str, payload: EventObservationRequest):
    def updater(event: dict) -> dict:
        obs = {
            "observation_id": f"obs_{uuid.uuid4().hex[:8]}",
            "vital_type": payload.vital_type,
            "value": payload.value,
            "unit": payload.unit,
            "note": payload.note,
            "recorded_by": payload.recorded_by or event.get("reporter") or "护工端",
            "recorded_at": _now_str(),
        }
        event.setdefault("observations", []).append(obs)
        detail = f"{payload.vital_type}：{payload.value}{payload.unit or ''}"
        if payload.note:
            detail += f"｜备注：{payload.note}"
        event.setdefault("execution_logs", []).append(_event_log("复测/观察记录", detail, obs["recorded_by"]))
        event["updated_at"] = _now_str()
        return event

    event = _update_event(event_id, updater)
    return {"code": 200, "message": "观察记录已保存", "event": event}


@router.get("/nursing/events/{event_id}/sbar", summary="获取护理事件 SBAR 交接单")
async def get_event_sbar(event_id: str):
    event = _get_event_or_404(event_id)
    return {"code": 200, "event_id": event_id, "handover_sbar": event.get("handover_sbar", {})}


@router.post("/nursing/events/{event_id}/archive", summary="归档护理事件：生成结构化入档记录")
async def archive_nursing_event(event_id: str):
    def updater(event: dict) -> dict:
        operator = event.get("reporter") or "护工端"
        event["progress"] = _task_progress(event)
        event["status"] = "archived"
        event["archived_at"] = _now_str()
        event["updated_at"] = _now_str()
        event.setdefault("execution_logs", []).append(_event_log("事件归档", "任务卡、护理建议、执行记录、复测观察和SBAR交接单已结构化入档", operator))
        event["archive_record"] = {
            "archive_id": f"arc_{uuid.uuid4().hex[:8]}",
            "event_id": event.get("event_id"),
            "patient_id": event.get("patient_id"),
            "patient_name": event.get("patient_name"),
            "bed_number": event.get("bed_number"),
            "risk_label": event.get("risk_label"),
            "event_type": event.get("event_type"),
            "raw_description": event.get("raw_description"),
            "nursing_advice": event.get("nursing_advice"),
            "tasks": event.get("immediate_tasks", []),
            "observations": event.get("observations", []),
            "execution_logs": event.get("execution_logs", []),
            "handover_sbar": event.get("handover_sbar", {}),
            "progress": event.get("progress", {}),
            "archived_at": event.get("archived_at"),
            "safety_boundary": event.get("safety_boundary"),
        }
        return event

    event = _update_event(event_id, updater)
    return {"code": 200, "message": "护理事件已归档并生成结构化入档记录", "event": event, "archive_record": event.get("archive_record")}
