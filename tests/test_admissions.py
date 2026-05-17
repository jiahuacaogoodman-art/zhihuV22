# -*- coding: utf-8 -*-
"""
入住流程（Admissions Workflow）测试

覆盖目标
  Part A · CareStore 数据层
            · _migrate() 老库兼容（缺列、缺索引时自动补齐）
            · 全流程持久化：create → assess → contract → pay → move-in → discharge
            · update_admission_status 状态机 + 时间线
            · get_admission_stats（院长统计）字段齐全 + 数学正确
  Part B · 路由 + 状态机（HTTP 层）
            · 全流程 6 步串通，HTTP 200 + 状态正确推进
            · 状态机非法转换 → 400 (不是 500，不是 409)
            · 缺失资源 → 404
            · PII 脱敏：身份证只回前3后4
  Part C · 经营统计 GET /api/admissions/stats
            · 默认 30 天窗口字段齐
            · days 校验范围（1..365）
            · 营收只算 status='completed'
            · 漏斗分析（by_status / by_referral / conversion）
            · occupancy_rate 除零保护
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ============================================================
# 共用：构建挂了 admissions 路由的 mini app
# ============================================================
def _prepare_admissions_app(tmp_path, user_store):
    """
    隔离的 mini FastAPI app，挂 admissions 路由。

    隔离点：
      · CareStore 单例切到 tmp_path/care.db
      · audit_log 单例切到 tmp_path/audit.db
      · admissions 模块的 audit 引用同步指到新单例
      · _sync_ehr_profile 短路掉（move-in 不会去碰 ChromaDB）
    """
    # 1. 切 CareStore 单例到 tmp 路径
    from app.services import care_store as cs_mod
    cs_mod.reset_care_store()
    care_db = tmp_path / "care.db"
    store = cs_mod.get_care_store(care_db)

    # 2. 切 audit_log 单例 + 让 admissions 模块用新单例
    from app.services import audit_log as audit_mod
    audit_mod.reset_audit_log()
    audit_db = tmp_path / "audit.db"
    audit_instance = audit_mod.get_audit_log(audit_db)
    from app.routers import admissions as adm_mod
    adm_mod.audit = audit_instance

    # 3. 短路 _sync_ehr_profile，避免 move-in 时尝试访问 ChromaDB
    adm_mod._sync_ehr_profile_backup = adm_mod._sync_ehr_profile
    adm_mod._sync_ehr_profile = lambda *a, **kw: None

    # 4. 组装 app
    from app.middleware.auth import AuthTokenMiddleware
    app = FastAPI()
    app.include_router(adm_mod.router, prefix="/api")
    app.add_middleware(
        AuthTokenMiddleware, legacy_token="", user_store=user_store
    )
    app.state.user_store = user_store
    app.state.auth_mode = "user_store" if user_store.has_users() else "disabled"
    return app, store, audit_instance, adm_mod


@pytest.fixture
def admissions_env(tmp_path, admin_store_and_token):
    """Yield 测试环境 + 在 teardown 还原所有 monkey-patch。"""
    user_store, admin_token = admin_store_and_token
    app, store, audit_instance, adm_mod = _prepare_admissions_app(
        tmp_path, user_store
    )

    # 预置一张床位（move-in 需要）
    store.create_bed({
        "bed_number": "TEST-001",
        "building": "A",
        "floor": "1",
        "room": "101",
        "bed_type": "standard",
    })
    bed = store.get_bed_by_number("TEST-001")

    # 预置一个护理等级（move-in 时分配）
    store.create_care_level({
        "level_key": "level_basic",
        "level_name": "基础护理",
        "daily_fee": 100.0,
    })

    yield {
        "app": app,
        "store": store,
        "audit": audit_instance,
        "user_store": user_store,
        "admin_token": admin_token,
        "bed_id": bed["bed_id"],
        "tmp_path": tmp_path,
    }

    # 还原
    adm_mod._sync_ehr_profile = adm_mod._sync_ehr_profile_backup
    from app.services import care_store as cs_mod
    cs_mod.reset_care_store()
    from app.services import audit_log as audit_mod
    audit_mod.reset_audit_log()


def _hdr(token: str) -> dict:
    return {"X-Auth-Token": token}


def _create_admission(client, token, **overrides) -> dict:
    payload = {
        "applicant_name": "测试老人",
        "applicant_gender": "男",
        "applicant_age": 78,
        "applicant_id_card": "110101194501011234",
        "applicant_phone": "13800138000",
        "guardian_name": "测试家属",
        "guardian_phone": "13900139000",
        "guardian_relation": "子女",
        "referral_source": "社区推荐",
        "health_summary": "高血压、糖尿病",
    }
    payload.update(overrides)
    r = client.post("/api/admissions", json=payload, headers=_hdr(token))
    assert r.status_code == 200, r.text
    return r.json()


# ============================================================
# Part A · CareStore 数据层
# ============================================================
class TestCareStoreMigration:
    """_migrate() —— 老库启动时必须把缺列/缺索引补齐，且 user_version 推进。"""

    def test_fresh_db_lands_on_target_version(self, tmp_path):
        """新建库：_init_db 写完整 schema 后，user_version 应推到 _SCHEMA_VERSION。"""
        from app.services.care_store import CareStore

        db = tmp_path / "fresh.db"
        store = CareStore(db)
        assert store._SCHEMA_VERSION >= 1

        with sqlite3.connect(db) as raw:
            uv = raw.execute("PRAGMA user_version").fetchone()[0]
        assert uv == store._SCHEMA_VERSION

    def test_migrate_adds_missing_columns_to_legacy_db(self, tmp_path):
        """模拟早期版本：admissions 表只有最小列，启动时应自动补齐 4 个离院字段。"""
        from app.services.care_store import CareStore

        db = tmp_path / "legacy.db"
        # 手动建一个**老版本**的 admissions 表 —— 没有 4 个离院财务字段
        with sqlite3.connect(db) as raw:
            raw.executescript("""
                CREATE TABLE admissions (
                    admission_id            TEXT PRIMARY KEY,
                    status                  TEXT NOT NULL DEFAULT 'inquiry',
                    applicant_name          TEXT NOT NULL,
                    applicant_gender        TEXT NOT NULL DEFAULT '',
                    applicant_age           INTEGER,
                    applicant_id_card       TEXT NOT NULL DEFAULT '',
                    applicant_phone         TEXT NOT NULL DEFAULT '',
                    guardian_name           TEXT NOT NULL DEFAULT '',
                    guardian_phone          TEXT NOT NULL DEFAULT '',
                    guardian_relation       TEXT NOT NULL DEFAULT '',
                    guardian_id_card        TEXT NOT NULL DEFAULT '',
                    health_summary          TEXT NOT NULL DEFAULT '',
                    care_needs              TEXT NOT NULL DEFAULT '',
                    preferred_room_type     TEXT NOT NULL DEFAULT '',
                    expected_admission_date TEXT NOT NULL DEFAULT '',
                    referral_source         TEXT NOT NULL DEFAULT '',
                    notes                   TEXT NOT NULL DEFAULT '',
                    assessment_id           TEXT NOT NULL DEFAULT '',
                    assessed_level          TEXT NOT NULL DEFAULT '',
                    assessment_conclusion   TEXT NOT NULL DEFAULT '',
                    assessed_at             TEXT NOT NULL DEFAULT '',
                    assessed_by             TEXT NOT NULL DEFAULT '',
                    contract_id             TEXT NOT NULL DEFAULT '',
                    contract_signed_at      TEXT NOT NULL DEFAULT '',
                    payment_id              TEXT NOT NULL DEFAULT '',
                    payment_status          TEXT NOT NULL DEFAULT '',
                    paid_at                 TEXT NOT NULL DEFAULT '',
                    patient_id              TEXT NOT NULL DEFAULT '',
                    bed_id                  TEXT NOT NULL DEFAULT '',
                    bed_number              TEXT NOT NULL DEFAULT '',
                    care_level_key          TEXT NOT NULL DEFAULT '',
                    actual_admission_date   TEXT NOT NULL DEFAULT '',
                    -- 故意不建 discharge_date / discharge_reason / settlement_amount / refund_amount
                    created_at              TEXT NOT NULL,
                    updated_at              TEXT NOT NULL
                );
                INSERT INTO admissions
                    (admission_id, applicant_name, created_at, updated_at)
                VALUES
                    ('adm_old_001', '老库申请人', '2024-01-01 00:00:00', '2024-01-01 00:00:00');
                PRAGMA user_version = 0;
            """)

        # CareStore 初始化时应自动迁移
        store = CareStore(db)

        # 1. 4 个离院字段应被补齐
        with sqlite3.connect(db) as raw:
            raw.row_factory = sqlite3.Row
            cols = {r["name"] for r in
                    raw.execute("PRAGMA table_info(admissions)").fetchall()}
            for needed in ("discharge_date", "discharge_reason",
                           "settlement_amount", "refund_amount"):
                assert needed in cols, f"_migrate 应补齐字段 {needed}"

            # 2. user_version 应被推进
            uv = raw.execute("PRAGMA user_version").fetchone()[0]
            assert uv == store._SCHEMA_VERSION

            # 3. 唯一索引应建好
            idx = {r["name"] for r in
                   raw.execute("PRAGMA index_list(admissions)").fetchall()}
            assert "idx_admissions_id_card_uniq" in idx

        # 4. 老数据应保留
        old = store.get_admission("adm_old_001")
        assert old is not None
        assert old["applicant_name"] == "老库申请人"
        # 新列默认值
        assert old["discharge_date"] == ""
        assert old["settlement_amount"] is None

    def test_migrate_is_idempotent(self, tmp_path):
        """重复初始化不能重复加列（ALTER ADD COLUMN 不幂等会报错）。"""
        from app.services.care_store import CareStore

        db = tmp_path / "idem.db"
        s1 = CareStore(db)  # 第一次：跑迁移
        s2 = CareStore(db)  # 第二次：user_version 已是 target，跳过
        assert s1._SCHEMA_VERSION == s2._SCHEMA_VERSION

        # 真正的幂等验证：discharge 操作不应该在已迁移的库里报错
        adm = s2.create_admission({"applicant_name": "幂等测试"})
        # 直接驱动到 active，再 discharge，验证 settlement_amount 列可写
        s2.update_admission_status(adm["admission_id"], "assessing")
        s2.update_admission_status(adm["admission_id"], "assessed")
        s2.update_admission_status(adm["admission_id"], "contracting")
        s2.update_admission_status(adm["admission_id"], "contracted")
        s2.update_admission_status(adm["admission_id"], "paying")
        s2.update_admission_status(adm["admission_id"], "paid")
        s2.update_admission_status(adm["admission_id"], "moving_in")
        s2.update_admission_status(adm["admission_id"], "active")
        result = s2.discharge(adm["admission_id"], {
            "settlement_amount": 1234.5,
            "refund_amount": 0.0,
            "discharge_reason": "正常离院",
        })
        assert result is not None
        assert result["status"] == "discharged"
        assert result["settlement_amount"] == 1234.5


class TestCareStoreAdmissionFlow:
    """直接驱动 CareStore 跑全流程，不经 HTTP 层。"""

    def test_full_flow_persists_all_artifacts(self, tmp_path):
        from app.services.care_store import CareStore

        store = CareStore(tmp_path / "care.db")
        # 准备床位 + 等级
        store.create_bed({"bed_number": "B1"})
        bed = store.get_bed_by_number("B1")
        store.create_care_level({"level_key": "lv1", "level_name": "一级护理"})

        # 1. create
        adm = store.create_admission(
            {"applicant_name": "张老人", "applicant_age": 80,
             "referral_source": "家属来访"},
            operator="tester",
        )
        assert adm["status"] == "inquiry"

        # 2. assess（自动推进到 assessed）
        store.update_admission_status(adm["admission_id"], "assessing",
                                      operator="tester")
        assessment = store.create_assessment(
            adm["admission_id"],
            {"recommended_level": "lv1", "conclusion": "可入住", "approved": True},
            operator="tester",
        )
        assert assessment["assessment_id"]
        adm = store.get_admission(adm["admission_id"])
        assert adm["status"] == "assessed"
        assert adm["assessed_level"] == "lv1"

        # 3. contract（自动推进到 contracted）
        store.update_admission_status(adm["admission_id"], "contracting",
                                      operator="tester")
        contract = store.create_contract(
            adm["admission_id"],
            {"start_date": "2026-01-01", "care_level_key": "lv1",
             "monthly_fee": 5000.0, "deposit": 10000.0},
            operator="tester",
        )
        assert contract["contract_number"].startswith("CTR-")
        adm = store.get_admission(adm["admission_id"])
        assert adm["status"] == "contracted"

        # 4. pay（自动推进到 paid）
        store.update_admission_status(adm["admission_id"], "paying",
                                      operator="tester")
        payment = store.create_payment(
            adm["admission_id"],
            {"amount": 10000.0, "payment_method": "wechat",
             "payment_type": "deposit"},
            operator="tester",
        )
        assert payment["amount"] == 10000.0
        adm = store.get_admission(adm["admission_id"])
        assert adm["status"] == "paid"

        # 5. move-in
        store.update_admission_status(adm["admission_id"], "moving_in",
                                      operator="tester")
        result = store.move_in(
            admission_id=adm["admission_id"],
            bed_id=bed["bed_id"],
            care_level_key="lv1",
            operator="tester",
        )
        assert result is not None
        assert result["status"] == "active"
        assert result["bed_number"] == "B1"
        assert result["patient_id"]
        # 床位应该被占用
        bed_after = store.get_bed(bed["bed_id"])
        assert bed_after["status"] == "occupied"

        # 6. discharge —— 床位应被释放，财务字段持久化
        result = store.discharge(
            adm["admission_id"],
            {"discharge_reason": "回家", "settlement_amount": 4500.0,
             "refund_amount": 5500.0},
            operator="tester",
        )
        assert result["status"] == "discharged"
        assert result["settlement_amount"] == 4500.0
        bed_after2 = store.get_bed(bed["bed_id"])
        assert bed_after2["status"] == "available"
        assert bed_after2["patient_id"] == ""

        # 时间线应有完整记录
        timeline = store.get_admission_timeline(adm["admission_id"])
        actions = [t["action"] for t in timeline]
        assert "创建入住申请" in actions
        assert "评估完成" in actions
        assert "合同签署" in actions
        assert "缴费完成" in actions
        assert "办理入住" in actions
        assert "办理离院" in actions

    def test_move_in_fails_when_bed_already_occupied(self, tmp_path):
        """床位被别人占了，move-in 应该返回 None（让路由层翻成 400）。"""
        from app.services.care_store import CareStore

        store = CareStore(tmp_path / "care.db")
        store.create_bed({"bed_number": "B1"})
        bed = store.get_bed_by_number("B1")
        # 先把床位手动占住
        store.assign_bed(bed["bed_id"], patient_id="P_existing",
                         patient_name="占座的人")

        adm = store.create_admission({"applicant_name": "新人"})
        # 直接把状态推到 paid（跳过中间步骤）
        for s in ("assessing", "assessed", "contracting", "contracted",
                  "paying", "paid", "moving_in"):
            store.update_admission_status(adm["admission_id"], s)

        result = store.move_in(adm["admission_id"], bed["bed_id"])
        assert result is None  # 床位已占 → 返回 None


class TestCareStoreStats:
    """get_admission_stats —— 数学正确 + 字段齐全 + 边界保护。"""

    def test_stats_empty_db(self, tmp_path):
        from app.services.care_store import CareStore

        store = CareStore(tmp_path / "care.db")
        stats = store.get_admission_stats(days=30)

        assert stats["total"] == 0
        assert stats["active_residents"] == 0
        assert stats["discharged"] == 0
        assert stats["by_status"] == {}
        assert stats["by_referral"] == {}
        assert stats["recent"]["new_admissions"] == 0
        assert stats["recent"]["revenue"] == 0.0
        assert stats["revenue_total"] == 0.0
        # 除零保护
        assert stats["occupancy"]["occupancy_rate"] is None
        assert stats["conversion"]["inquiry_to_active"] is None

    def test_stats_arithmetic(self, tmp_path):
        from app.services.care_store import CareStore

        store = CareStore(tmp_path / "care.db")
        # 准备 4 张床位、2 张被占
        for i in range(1, 5):
            store.create_bed({"bed_number": f"S-{i}"})
        bed_ids = [b["bed_id"] for b in store.list_beds()]
        store.assign_bed(bed_ids[0], "P1", "甲")
        store.assign_bed(bed_ids[1], "P2", "乙")

        # 5 条入住申请：3 inquiry / 1 active / 1 discharged
        for i in range(3):
            store.create_admission({
                "applicant_name": f"咨询者{i}",
                "referral_source": "网络" if i % 2 == 0 else "社区推荐",
            })
        a_active = store.create_admission(
            {"applicant_name": "活跃住户", "referral_source": "医院转介"}
        )
        for s in ("assessing", "assessed", "contracting", "contracted",
                  "paying", "paid", "moving_in", "active"):
            store.update_admission_status(a_active["admission_id"], s)

        a_discharged = store.create_admission(
            {"applicant_name": "已离院", "referral_source": "家属来访"}
        )
        for s in ("assessing", "assessed", "contracting", "contracted",
                  "paying", "paid", "moving_in", "active", "discharged"):
            store.update_admission_status(a_discharged["admission_id"], s)

        # 两笔 payment：直接 SQL 插入，避免 create_payment 的副作用
        # （create_payment 会把 admission.status 重置为 'paid'，污染本测试的状态分布）
        # 一条已完成 5000，一条已退款 9999 → 营收应只算 5000
        with store._connect() as conn:
            conn.execute(
                "INSERT INTO payments (payment_id, admission_id, contract_id, "
                "payment_type, amount, payment_method, status, paid_at, created_at) "
                "VALUES ('pay_completed', ?, '', 'monthly', 5000.0, 'wechat', "
                "'completed', ?, ?)",
                (a_active["admission_id"], store._now(), store._now()),
            )
            conn.execute(
                "INSERT INTO payments (payment_id, admission_id, contract_id, "
                "payment_type, amount, payment_method, status, paid_at, created_at) "
                "VALUES ('pay_refunded', ?, '', 'monthly', 9999.0, 'cash', "
                "'refunded', '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
                (a_active["admission_id"],),
            )

        stats = store.get_admission_stats(days=365)

        # ── 计数核验 ──
        assert stats["total"] == 5
        assert stats["active_residents"] == 1
        assert stats["discharged"] == 1
        assert stats["by_status"].get("inquiry") == 3
        assert stats["by_status"].get("active") == 1
        assert stats["by_status"].get("discharged") == 1
        # 来源渠道：网络 2 / 社区推荐 1 / 医院转介 1 / 家属来访 1
        assert stats["by_referral"].get("网络") == 2
        assert stats["by_referral"].get("社区推荐") == 1
        assert stats["by_referral"].get("医院转介") == 1
        assert stats["by_referral"].get("家属来访") == 1

        # ── 营收核验：refunded 不计 ──
        assert stats["revenue_total"] == 5000.0  # 9999 已退款不算

        # ── 床位占用：2 / 4 ──
        assert stats["occupancy"]["occupied_beds"] == 2
        assert stats["occupancy"]["total_beds"] == 4
        assert stats["occupancy"]["occupancy_rate"] == 0.5

        # ── 转化率：(active + discharged) / total = 2 / 5 ──
        assert stats["conversion"]["inquiry_to_active"] == 0.4

    def test_stats_referral_groups_empty_to_unfilled(self, tmp_path):
        """referral_source 为空时应聚合到 '未填写' bucket。"""
        from app.services.care_store import CareStore

        store = CareStore(tmp_path / "care.db")
        store.create_admission({"applicant_name": "甲"})  # 默认空
        store.create_admission({"applicant_name": "乙", "referral_source": ""})
        store.create_admission({"applicant_name": "丙", "referral_source": "网络"})

        stats = store.get_admission_stats()
        assert stats["by_referral"].get("未填写") == 2
        assert stats["by_referral"].get("网络") == 1


# ============================================================
# Part B · 路由层 + 状态机
# ============================================================
class TestAdmissionsCreateAndList:
    def test_create_and_get_admission(self, admissions_env):
        env = admissions_env
        with TestClient(env["app"]) as c:
            adm = _create_admission(c, env["admin_token"])
            assert adm["status"] == "inquiry"
            assert adm["admission_id"].startswith("adm_")

            # PII 脱敏：身份证号应该 mask 掉
            assert "*" in adm["applicant_id_card"]
            assert adm["applicant_id_card"].startswith("110")
            assert adm["applicant_id_card"].endswith("1234")

            # GET 单个
            r = c.get(f"/api/admissions/{adm['admission_id']}",
                      headers=_hdr(env["admin_token"]))
            assert r.status_code == 200
            assert r.json()["applicant_name"] == "测试老人"

    def test_get_nonexistent_returns_404(self, admissions_env):
        env = admissions_env
        with TestClient(env["app"]) as c:
            r = c.get("/api/admissions/adm_does_not_exist",
                      headers=_hdr(env["admin_token"]))
        assert r.status_code == 404

    def test_list_admissions_with_status_filter(self, admissions_env):
        env = admissions_env
        with TestClient(env["app"]) as c:
            _create_admission(c, env["admin_token"], applicant_name="甲")
            _create_admission(c, env["admin_token"], applicant_name="乙",
                              applicant_id_card="220101194501012222")

            r = c.get("/api/admissions?status=inquiry&limit=10",
                     headers=_hdr(env["admin_token"]))
            assert r.status_code == 200
            data = r.json()
            assert data["total"] >= 2
            assert all(a["status"] == "inquiry" for a in data["admissions"])

    def test_list_invalid_status_returns_422(self, admissions_env):
        """非法 status 参数应该被 Pydantic 校住。"""
        env = admissions_env
        with TestClient(env["app"]) as c:
            r = c.get("/api/admissions?status=garbage",
                     headers=_hdr(env["admin_token"]))
        assert r.status_code == 422


class TestAdmissionsFullFlow:
    """端到端：6 步走完，HTTP 层每步都返回 200，状态正确推进。"""

    def test_complete_flow_create_to_discharge(self, admissions_env):
        env = admissions_env
        with TestClient(env["app"]) as c:
            # 1. create
            adm = _create_admission(c, env["admin_token"])
            adm_id = adm["admission_id"]

            # 2. assess
            r = c.post(f"/api/admissions/{adm_id}/assess",
                       json={"recommended_level": "level_basic",
                             "conclusion": "适合入住", "approved": True,
                             "adl_score": 70},
                       headers=_hdr(env["admin_token"]))
            assert r.status_code == 200, r.text
            adm = c.get(f"/api/admissions/{adm_id}",
                        headers=_hdr(env["admin_token"])).json()
            assert adm["status"] == "assessed"

            # 3. contract
            r = c.post(f"/api/admissions/{adm_id}/contract",
                       json={"start_date": "2026-01-01",
                             "care_level_key": "level_basic",
                             "monthly_fee": 5000.0, "deposit": 10000.0},
                       headers=_hdr(env["admin_token"]))
            assert r.status_code == 200, r.text
            adm = c.get(f"/api/admissions/{adm_id}",
                        headers=_hdr(env["admin_token"])).json()
            assert adm["status"] == "contracted"

            # 4. pay
            r = c.post(f"/api/admissions/{adm_id}/pay",
                       json={"amount": 10000.0, "payment_method": "wechat",
                             "payment_type": "deposit"},
                       headers=_hdr(env["admin_token"]))
            assert r.status_code == 200, r.text
            adm = c.get(f"/api/admissions/{adm_id}",
                        headers=_hdr(env["admin_token"])).json()
            assert adm["status"] == "paid"

            # 5. move-in
            r = c.post(f"/api/admissions/{adm_id}/move-in",
                       json={"bed_id": env["bed_id"],
                             "care_level_key": "level_basic"},
                       headers=_hdr(env["admin_token"]))
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "active"
            assert r.json()["bed_number"] == "TEST-001"

            # 6. discharge
            r = c.post(f"/api/admissions/{adm_id}/discharge",
                       json={"discharge_reason": "回家",
                             "settlement_amount": 4500.0,
                             "refund_amount": 5500.0},
                       headers=_hdr(env["admin_token"]))
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "discharged"

            # 时间线
            r = c.get(f"/api/admissions/{adm_id}/timeline",
                    headers=_hdr(env["admin_token"]))
            assert r.status_code == 200
            actions = [t["action"] for t in r.json()["timeline"]]
            # 至少应该有这几个关键节点
            assert any("评估" in a for a in actions)
            assert any("合同" in a for a in actions)
            assert any("缴费" in a for a in actions)
            assert any("入住" in a for a in actions)
            assert any("离院" in a for a in actions)


class TestStateMachineRejection:
    """状态机非法转换：必须返回 400，且说明允许的目标状态。"""

    def test_pay_before_contract_rejected(self, admissions_env):
        """inquiry 状态下直接 pay → 400（必须先评估+签约）。"""
        env = admissions_env
        with TestClient(env["app"]) as c:
            adm = _create_admission(c, env["admin_token"])
            r = c.post(f"/api/admissions/{adm['admission_id']}/pay",
                       json={"amount": 5000.0, "payment_method": "cash"},
                       headers=_hdr(env["admin_token"]))
        assert r.status_code == 400
        body = r.json()
        # 错误信息应明确说明当前状态 + 允许的目标状态
        msg = body.get("detail") or body.get("message") or ""
        assert "inquiry" in msg or "不允许" in msg

    def test_move_in_before_pay_rejected(self, admissions_env):
        """assessed 状态下尝试 move-in → 400。"""
        env = admissions_env
        with TestClient(env["app"]) as c:
            adm = _create_admission(c, env["admin_token"])
            adm_id = adm["admission_id"]
            # 推进到 assessed
            r = c.post(f"/api/admissions/{adm_id}/assess",
                       json={"recommended_level": "level_basic",
                             "conclusion": "ok"},
                       headers=_hdr(env["admin_token"]))
            assert r.status_code == 200

            # 直接 move-in（跳过 contract+pay）
            r = c.post(f"/api/admissions/{adm_id}/move-in",
                       json={"bed_id": env["bed_id"]},
                       headers=_hdr(env["admin_token"]))
        assert r.status_code == 400

    def test_discharge_before_active_rejected(self, admissions_env):
        """inquiry 状态下尝试 discharge → 400（only active 可 discharge）。"""
        env = admissions_env
        with TestClient(env["app"]) as c:
            adm = _create_admission(c, env["admin_token"])
            r = c.post(f"/api/admissions/{adm['admission_id']}/discharge",
                       json={"discharge_reason": "test"},
                       headers=_hdr(env["admin_token"]))
        assert r.status_code == 400

    def test_status_change_invalid_transition(self, admissions_env):
        """PATCH .../status 走非法跃迁 inquiry → active 应该 400。"""
        env = admissions_env
        with TestClient(env["app"]) as c:
            adm = _create_admission(c, env["admin_token"])
            r = c.patch(f"/api/admissions/{adm['admission_id']}/status",
                       json={"target_status": "active",
                             "reason": "test invalid"},
                       headers=_hdr(env["admin_token"]))
        assert r.status_code == 400

    def test_status_change_legal_cancellation(self, admissions_env):
        """合法跃迁：inquiry → cancelled 必须放行。"""
        env = admissions_env
        with TestClient(env["app"]) as c:
            adm = _create_admission(c, env["admin_token"])
            r = c.patch(f"/api/admissions/{adm['admission_id']}/status",
                       json={"target_status": "cancelled",
                             "reason": "家属放弃"},
                       headers=_hdr(env["admin_token"]))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cancelled"

    def test_assessment_failure_returns_to_inquiry(self, admissions_env):
        """评估不通过(approved=False)：状态应回退到 inquiry。"""
        env = admissions_env
        with TestClient(env["app"]) as c:
            adm = _create_admission(c, env["admin_token"])
            adm_id = adm["admission_id"]
            r = c.post(f"/api/admissions/{adm_id}/assess",
                       json={"recommended_level": "level_basic",
                             "conclusion": "评估不达标",
                             "approved": False},
                       headers=_hdr(env["admin_token"]))
            assert r.status_code == 200
            adm = c.get(f"/api/admissions/{adm_id}",
                        headers=_hdr(env["admin_token"])).json()
        # 评估未通过：状态回到 inquiry
        assert adm["status"] == "inquiry"

    def test_move_in_with_nonexistent_bed_returns_400(self, admissions_env):
        """走到 paid 后用一个不存在的 bed_id move-in，store 返回 None → 400。"""
        env = admissions_env
        with TestClient(env["app"]) as c:
            adm = _create_admission(c, env["admin_token"])
            adm_id = adm["admission_id"]
            # 推到 paid
            c.post(f"/api/admissions/{adm_id}/assess",
                   json={"recommended_level": "level_basic", "conclusion": "ok"},
                   headers=_hdr(env["admin_token"]))
            c.post(f"/api/admissions/{adm_id}/contract",
                   json={"start_date": "2026-01-01",
                         "care_level_key": "level_basic", "monthly_fee": 5000},
                   headers=_hdr(env["admin_token"]))
            c.post(f"/api/admissions/{adm_id}/pay",
                   json={"amount": 10000, "payment_method": "cash"},
                   headers=_hdr(env["admin_token"]))
            r = c.post(f"/api/admissions/{adm_id}/move-in",
                       json={"bed_id": "bed_does_not_exist"},
                       headers=_hdr(env["admin_token"]))
        assert r.status_code == 400


class TestAuthAndPermissions:
    def test_missing_token_returns_401(self, admissions_env):
        env = admissions_env
        with TestClient(env["app"]) as c:
            r = c.get("/api/admissions")
        assert r.status_code == 401

    def test_invalid_token_returns_401(self, admissions_env):
        env = admissions_env
        with TestClient(env["app"]) as c:
            r = c.get("/api/admissions",
                     headers={"X-Auth-Token": "garbage-token"})
        assert r.status_code == 401


# ============================================================
# Part C · 经营统计 GET /api/admissions/stats
# ============================================================
class TestAdmissionStatsEndpoint:
    def test_stats_empty(self, admissions_env):
        env = admissions_env
        with TestClient(env["app"]) as c:
            r = c.get("/api/admissions/stats", headers=_hdr(env["admin_token"]))
        assert r.status_code == 200, r.text
        body = r.json()
        # 必备字段
        for key in ("total", "active_residents", "discharged",
                    "by_status", "by_referral",
                    "recent", "revenue_total",
                    "occupancy", "conversion"):
            assert key in body
        assert body["total"] == 0
        assert body["recent"]["period"] == "近30天"
        # fixture 预置了 1 张床位但没人入住 → 占用率 0.0
        assert body["occupancy"]["total_beds"] == 1
        assert body["occupancy"]["occupied_beds"] == 0
        assert body["occupancy"]["occupancy_rate"] == 0.0
        # 没申请 → 转化率 None（除零保护）
        assert body["conversion"]["inquiry_to_active"] is None

    def test_stats_after_full_flow(self, admissions_env):
        env = admissions_env
        with TestClient(env["app"]) as c:
            # 1) 仅咨询的 2 条
            _create_admission(c, env["admin_token"],
                              applicant_name="只咨询甲",
                              applicant_id_card="110101194501019991",
                              referral_source="网络")
            _create_admission(c, env["admin_token"],
                              applicant_name="只咨询乙",
                              applicant_id_card="110101194501019992",
                              referral_source="社区推荐")

            # 2) 走完全流程的 1 条 → active
            adm = _create_admission(c, env["admin_token"],
                                    applicant_name="完整流程",
                                    applicant_id_card="110101194501019993",
                                    referral_source="医院转介")
            adm_id = adm["admission_id"]
            c.post(f"/api/admissions/{adm_id}/assess",
                   json={"recommended_level": "level_basic", "conclusion": "ok"},
                   headers=_hdr(env["admin_token"]))
            c.post(f"/api/admissions/{adm_id}/contract",
                   json={"start_date": "2026-01-01",
                         "care_level_key": "level_basic",
                         "monthly_fee": 5000.0},
                   headers=_hdr(env["admin_token"]))
            c.post(f"/api/admissions/{adm_id}/pay",
                   json={"amount": 10000.0, "payment_method": "wechat"},
                   headers=_hdr(env["admin_token"]))
            c.post(f"/api/admissions/{adm_id}/move-in",
                   json={"bed_id": env["bed_id"],
                         "care_level_key": "level_basic"},
                   headers=_hdr(env["admin_token"]))

            r = c.get("/api/admissions/stats", headers=_hdr(env["admin_token"]))

        assert r.status_code == 200, r.text
        body = r.json()
        # 总数 = 3
        assert body["total"] == 3
        # 1 个 active
        assert body["active_residents"] == 1
        # 状态分布
        assert body["by_status"].get("inquiry") == 2
        assert body["by_status"].get("active") == 1
        # 来源
        assert body["by_referral"].get("网络") == 1
        assert body["by_referral"].get("社区推荐") == 1
        assert body["by_referral"].get("医院转介") == 1
        # 营收：完成的 1 万押金
        assert body["revenue_total"] == 10000.0
        assert body["recent"]["revenue"] == 10000.0
        assert body["recent"]["new_admissions"] == 3
        # 占用：1/1 = 1.0（fixture 只创建了一张床位）
        assert body["occupancy"]["total_beds"] == 1
        assert body["occupancy"]["occupied_beds"] == 1
        assert body["occupancy"]["occupancy_rate"] == 1.0
        # 转化率：1/3 ≈ 0.3333
        assert body["conversion"]["inquiry_to_active"] == round(1 / 3, 4)

    def test_stats_days_param_validation(self, admissions_env):
        env = admissions_env
        with TestClient(env["app"]) as c:
            # days=0 不合法（ge=1）
            r = c.get("/api/admissions/stats?days=0",
                     headers=_hdr(env["admin_token"]))
            assert r.status_code == 422

            # days=400 超过 365
            r = c.get("/api/admissions/stats?days=400",
                     headers=_hdr(env["admin_token"]))
            assert r.status_code == 422

            # days=7 合法
            r = c.get("/api/admissions/stats?days=7",
                     headers=_hdr(env["admin_token"]))
            assert r.status_code == 200
            assert r.json()["recent"]["period"] == "近7天"

    def test_stats_requires_auth(self, admissions_env):
        env = admissions_env
        with TestClient(env["app"]) as c:
            r = c.get("/api/admissions/stats")
        assert r.status_code == 401
