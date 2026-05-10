# -*- coding: utf-8 -*-
"""
@File    : services/decision_memory.py
@Desc    : L4 决策记忆（Decision Memory）
           - 每次 AI 决策 → 写入 ChromaDB，doc_type=decision_log
           - 引用的 evidence_id / doc_id 一并保存，形成可追溯链
           - 下一次同病人检索时，HybridRetriever 会自然地把过去决策
             当作一类证据检索回来，从而让模型"看见"自己上一次的判断
           - 支持 outcome 补丁（护工执行完任务后反馈"实际结果"）
           - 支持按 patient_id + 时间范围列出决策历史
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any


DECISION_DOC_TYPE = "decision_log"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _scalar(v: Any) -> Any:
    """Chroma metadata 只支持 str/int/float/bool/None，其他一律 JSON 序列化。"""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return str(v)


def _build_decision_document(
    patient_id: str,
    patient_name: str,
    symptom: str,
    advice: str,
    event_type: str | None,
    risk_level: str | None,
) -> str:
    """决策文档进入向量库后，下次检索'类似症状'时会被召回。所以文本要把
    症状和结论都放进去，不能只放总结。"""
    parts = [
        f"【过往护理决策】患者：{patient_name or patient_id}（{patient_id}）",
    ]
    if event_type:
        parts.append(f"事件类型：{event_type}")
    if risk_level:
        parts.append(f"风险等级：{risk_level}")
    parts.append(f"当时主诉：{symptom}")
    parts.append(f"AI 建议（节选）：{(advice or '')[:400]}")
    return "\n".join(parts)


class DecisionMemory:
    """决策记忆读写层。把 ChromaDB collection 当成唯一事实存储。"""

    def __init__(self, collection, embedding_function):
        self.collection = collection
        self.embedding_function = embedding_function

    # ── 写入 ──────────────────────────────────────────────────
    def log_decision(
        self,
        patient_id: str,
        symptom: str,
        advice: str,
        evidence: list[dict] | None = None,
        *,
        patient_name: str = "",
        event_type: str | None = None,
        risk_level: str | None = None,
        event_id: str | None = None,
        decision_source: str = "nursing_decision",
    ) -> dict:
        decision_id = f"dec_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        timestamp = _now()
        doc_text = _build_decision_document(
            patient_id=patient_id,
            patient_name=patient_name,
            symptom=symptom,
            advice=advice,
            event_type=event_type,
            risk_level=risk_level,
        )
        evidence = evidence or []
        # 只保存检索最需要的字段，避免 metadata 爆炸
        evidence_refs = [
            {
                "evidence_id": e.get("evidence_id"),
                "doc_id": e.get("doc_id"),
                "source_type": e.get("source_type"),
                "source_label": e.get("source_label"),
            }
            for e in evidence
        ]
        metadata = {
            "patient_id": patient_id,
            "name": patient_name,
            "doc_type": DECISION_DOC_TYPE,
            "source_type": DECISION_DOC_TYPE,
            "decision_id": decision_id,
            "decision_source": decision_source,
            "event_type": event_type or "",
            "risk_level": risk_level or "",
            "event_id": event_id or "",
            "timestamp": timestamp,
            "created_at": timestamp,
            "symptom": symptom,
            # advice 只存前 1200 字；完整文本已经在 document 里
            "advice_preview": (advice or "")[:1200],
            "evidence_refs": _scalar(evidence_refs),
            "outcome_status": "pending",     # pending / effective / ineffective / partial
            "outcome_note": "",
            "outcome_recorded_at": "",
            "outcome_recorded_by": "",
        }
        try:
            embedding = self.embedding_function.encode(doc_text).tolist()
            self.collection.add(
                ids=[decision_id],
                documents=[doc_text],
                embeddings=[embedding],
                metadatas=[metadata],
            )
        except Exception as e:
            # 决策记忆是"锦上添花"，不能把主流程搞挂掉
            return {"decision_id": decision_id, "status": "log_failed", "error": str(e)}
        return {
            "decision_id": decision_id,
            "patient_id": patient_id,
            "timestamp": timestamp,
            "evidence_refs": evidence_refs,
            "status": "logged",
        }

    # ── outcome 补丁 ─────────────────────────────────────────
    def record_outcome(
        self,
        decision_id: str,
        outcome_status: str,
        note: str = "",
        recorded_by: str = "",
    ) -> dict:
        """护工/护士在执行完 AI 建议后，回填"实际效果"。
        这样下一次检索把该决策召回时，Prompt 能同时看到"当时建议什么 + 后来实际如何"。"""
        allowed = {"effective", "ineffective", "partial", "pending"}
        if outcome_status not in allowed:
            raise ValueError(f"outcome_status 必须是 {allowed}")
        result = self.collection.get(ids=[decision_id], include=["documents", "metadatas"])
        ids = result.get("ids", []) or []
        if not ids:
            raise LookupError(f"未找到 decision_id={decision_id}")
        meta = (result.get("metadatas") or [{}])[0] or {}
        doc = (result.get("documents") or [""])[0] or ""
        meta["outcome_status"] = outcome_status
        meta["outcome_note"] = note or ""
        meta["outcome_recorded_at"] = _now()
        meta["outcome_recorded_by"] = recorded_by or ""
        # 把结果追加到可检索文本里，下次向量检索也能匹配"实际无效"之类的词
        outcome_line = f"\n实际执行结果：{outcome_status}"
        if note:
            outcome_line += f"（{note}）"
        outcome_line += f"｜记录于 {meta['outcome_recorded_at']}"
        new_doc = doc
        # 替换旧的结果行（幂等），没有则追加
        if "实际执行结果：" in new_doc:
            parts = new_doc.split("实际执行结果：")
            new_doc = parts[0].rstrip() + outcome_line
        else:
            new_doc = new_doc + outcome_line
        try:
            embedding = self.embedding_function.encode(new_doc).tolist()
            self.collection.update(
                ids=[decision_id],
                documents=[new_doc],
                embeddings=[embedding],
                metadatas=[meta],
            )
        except Exception as e:
            raise RuntimeError(f"决策记忆更新失败：{e}") from e
        return {
            "decision_id": decision_id,
            "outcome_status": outcome_status,
            "outcome_note": note,
            "outcome_recorded_at": meta["outcome_recorded_at"],
            "outcome_recorded_by": meta["outcome_recorded_by"],
        }

    # ── 读取 ──────────────────────────────────────────────────
    def get_decision(self, decision_id: str) -> dict | None:
        result = self.collection.get(ids=[decision_id], include=["documents", "metadatas"])
        ids = result.get("ids", []) or []
        if not ids:
            return None
        return self._to_dict(ids[0], result["documents"][0], result["metadatas"][0])

    def list_decisions(
        self,
        patient_id: str | None = None,
        limit: int = 20,
        days: int | None = None,
    ) -> list[dict]:
        where: dict = {"doc_type": {"$eq": DECISION_DOC_TYPE}}
        if patient_id:
            # Chroma 不支持多字段同时 $eq 直接写（得用 $and）
            where = {
                "$and": [
                    {"doc_type": {"$eq": DECISION_DOC_TYPE}},
                    {"patient_id": {"$eq": patient_id}},
                ]
            }
        result = self.collection.get(where=where, include=["documents", "metadatas"])
        ids = result.get("ids", []) or []
        docs = result.get("documents", []) or []
        metas = result.get("metadatas", []) or []
        records = [self._to_dict(i, d, m) for i, d, m in zip(ids, docs, metas)]
        records.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
        if days is not None:
            cutoff = datetime.now() - timedelta(days=days)
            records = [
                r for r in records
                if r.get("timestamp") and self._parse_ts(r["timestamp"]) >= cutoff
            ]
        return records[:limit]

    # ── helpers ─────────────────────────────────────────────
    @staticmethod
    def _parse_ts(s: str) -> datetime:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.min

    @staticmethod
    def _to_dict(doc_id: str, doc: str, meta: dict) -> dict:
        meta = meta or {}
        refs_raw = meta.get("evidence_refs")
        if isinstance(refs_raw, str):
            try:
                refs = json.loads(refs_raw)
            except Exception:
                refs = []
        else:
            refs = refs_raw or []
        return {
            "decision_id": meta.get("decision_id") or doc_id,
            "patient_id": meta.get("patient_id"),
            "patient_name": meta.get("name"),
            "timestamp": meta.get("timestamp") or meta.get("created_at"),
            "symptom": meta.get("symptom"),
            "advice_preview": meta.get("advice_preview") or doc,
            "event_type": meta.get("event_type"),
            "risk_level": meta.get("risk_level"),
            "event_id": meta.get("event_id"),
            "decision_source": meta.get("decision_source"),
            "evidence_refs": refs,
            "outcome_status": meta.get("outcome_status") or "pending",
            "outcome_note": meta.get("outcome_note") or "",
            "outcome_recorded_at": meta.get("outcome_recorded_at") or "",
            "outcome_recorded_by": meta.get("outcome_recorded_by") or "",
        }


def format_memory_block(records: list[dict], max_items: int = 3) -> str:
    """把最近 N 条决策格式化为 Prompt 提示块；模型看到这个自然会引用'上次'。"""
    if not records:
        return ""
    top = records[:max_items]
    lines = ["【该患者近期 AI 决策回忆】（供参考，避免与已验证无效的方案重复）"]
    for r in top:
        ts = r.get("timestamp") or "—"
        sym = (r.get("symptom") or "").strip()[:50]
        adv = (r.get("advice_preview") or "").strip().replace("\n", " ")[:120]
        outcome = r.get("outcome_status") or "pending"
        outcome_map = {
            "effective": "实际有效",
            "ineffective": "实际无效",
            "partial": "部分有效",
            "pending": "未回填结果",
        }
        note = r.get("outcome_note") or ""
        line = f"- {ts}｜主诉「{sym}」｜当时建议：{adv}｜执行结果：{outcome_map.get(outcome, outcome)}"
        if note:
            line += f"（{note}）"
        lines.append(line)
    return "\n".join(lines)
