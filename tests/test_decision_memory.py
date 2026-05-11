# -*- coding: utf-8 -*-
"""
冒烟测试 3：决策记忆（EventStore + DecisionMemory）纯单元测试
- EventStore 的 save / get / update 基本行为
- DecisionMemory.log_decision 写入成功并返回 decision_id
- OutcomeRecordRequest schema 拒绝非法枚举值

这两个测试完全不依赖 FastAPI TestClient，也不需要 Chroma 真实连接。
"""
import tempfile, sys
import pytest
from unittest.mock import MagicMock
import numpy as np


# ── EventStore 单元测试 ──────────────────────────────────────
class TestEventStore:
    def setup_method(self):
        import tempfile, os
        self._tmp = tempfile.mkdtemp()
        self._db = f"{self._tmp}/events.db"

    def _store(self):
        from app.services.event_store import EventStore
        return EventStore(self._db)

    def test_save_and_get(self):
        store = self._store()
        ev = {"event_id": "e1", "patient_id": "p1", "status": "processing",
              "created_at": "2026-01-01 00:00:00"}
        store.save_event(ev)
        got = store.get_event("e1")
        assert got is not None
        assert got["event_id"] == "e1"

    def test_upsert_updates_status(self):
        store = self._store()
        ev = {"event_id": "e2", "patient_id": "p1", "status": "processing",
              "created_at": "2026-01-01 00:00:00"}
        store.save_event(ev)
        ev["status"] = "archived"
        store.save_event(ev)
        assert store.get_event("e2")["status"] == "archived"

    def test_update_event_atomic(self):
        store = self._store()
        ev = {"event_id": "e3", "patient_id": "p1", "status": "processing",
              "created_at": "2026-01-01 00:00:00"}
        store.save_event(ev)
        updated = store.update_event("e3", lambda e: {**e, "status": "done"})
        assert updated["status"] == "done"
        assert store.get_event("e3")["status"] == "done"

    def test_update_missing_raises_keyerror(self):
        store = self._store()
        with pytest.raises(KeyError):
            store.update_event("nonexistent", lambda e: e)

    def test_get_missing_returns_none(self):
        store = self._store()
        assert store.get_event("ghost") is None

    def test_load_with_filters(self):
        store = self._store()
        for i in range(5):
            store.save_event({
                "event_id": f"ef{i}", "patient_id": f"p{i % 2}",
                "status": "done" if i % 2 == 0 else "processing",
                "created_at": "2026-01-01 00:00:00",
            })
        p0 = store.load_events(patient_id="p0")
        assert all(e["patient_id"] == "p0" for e in p0)
        done = store.load_events(status_filter="done")
        assert all(e["status"] == "done" for e in done)


# ── DecisionMemory 单元测试 ──────────────────────────────────
class TestDecisionMemory:
    def _make_deps(self):
        """返回一对 (mock_collection, mock_embedding_fn)，不需要真实 Chroma。"""
        col = MagicMock()
        col.add = MagicMock()
        col.get = MagicMock(return_value={"ids": [], "documents": [], "metadatas": []})

        emb = MagicMock()
        emb.encode = MagicMock(return_value=np.zeros(512, dtype=np.float32))
        return col, emb

    def test_log_decision_returns_decision_id(self):
        from app.services.decision_memory import DecisionMemory
        col, emb = self._make_deps()
        dm = DecisionMemory(col, emb)
        result = dm.log_decision(
            patient_id="p001",
            symptom="头晕出汗",
            advice="建议测血糖",
            patient_name="张三",
        )
        assert "decision_id" in result
        assert result["decision_id"].startswith("dec_")
        assert result.get("status") == "logged"
        col.add.assert_called_once()

    def test_log_decision_chroma_failure_does_not_raise(self):
        """Chroma 写失败时决策记忆走降级路径，不抛异常，主流程不中断。"""
        from app.services.decision_memory import DecisionMemory
        col, emb = self._make_deps()
        col.add.side_effect = RuntimeError("chroma is down")
        dm = DecisionMemory(col, emb)
        result = dm.log_decision(patient_id="p001", symptom="x", advice="y")
        assert result.get("status") == "log_failed"


# ── Schema 单元测试 ──────────────────────────────────────────
class TestSchemas:
    def test_outcome_rejects_invalid_status(self):
        from app.models.schemas import OutcomeRecordRequest
        import pydantic
        with pytest.raises((pydantic.ValidationError, ValueError)):
            OutcomeRecordRequest(outcome_status="invalid_value")

    def test_outcome_accepts_valid_statuses(self):
        from app.models.schemas import OutcomeRecordRequest
        for s in ("effective", "ineffective", "partial", "pending"):
            req = OutcomeRecordRequest(outcome_status=s)
            assert req.outcome_status == s
