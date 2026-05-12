# -*- coding: utf-8 -*-
"""
PR #49 · Billing P1 测试

覆盖范围
  Part A  BillingStore 单元测试（三张表 + 状态机 + 边界）
  Part B  REST 接口集成测试（路由 + 权限守卫）
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.middleware.auth import AuthTokenMiddleware
from app.routers import billing as billing_router
from app.services.billing_store import (
    AdmissionHasBillsError,
    AdmissionNotFoundError,
    BillingStore,
    BillExistsError,
    BillVoidedError,
    PaymentAlreadyVoidedError,
    PaymentExceedsRemainingError,
    PaymentNotFoundError,
)
from app.services.permissions import (
    BUILTIN_ROLE_ADMIN,
    BUILTIN_ROLE_FINANCE,
    BUILTIN_ROLE_NURSE,
    PERM_BILLING_READ,
    PERM_BILLING_WRITE,
)
from app.services.user_store import UserStore


# ── Fixtures ─────────────────────────────────────────────
@pytest.fixture
def billing_store(tmp_path):
    return BillingStore(tmp_path / "billing.db")


@pytest.fixture
def populated_billing(billing_store):
    """返回 (store, admission, bill)。"""
    adm = billing_store.create_admission(
        "p001", "张三", "2026-01-15",
        bed_number="A201", care_level="二级", monthly_fee=3800.0,
    )
    bill = billing_store.create_bill(
        adm.admission_id, "2026-02",
        bed_fee=2000.0, care_fee=1500.0, other_fee=300.0,
    )
    return billing_store, adm, bill


# =============================================================
# Part A — BillingStore 单元测试
# =============================================================
class TestAdmissionCrud:
    def test_create_and_get(self, billing_store):
        adm = billing_store.create_admission(
            "p001", "张三", "2026-01-15",
            bed_number="A201", monthly_fee=3800.0,
        )
        assert adm.patient_id == "p001"
        assert adm.is_active
        assert adm.monthly_fee == 3800.0
        fetched = billing_store.get_admission(adm.admission_id)
        assert fetched is not None
        assert fetched.admission_id == adm.admission_id

    def test_list_filter_active(self, billing_store):
        a1 = billing_store.create_admission("p1", "A", "2026-01-01")
        a2 = billing_store.create_admission("p2", "B", "2026-02-01")
        billing_store.discharge(a1.admission_id, "2026-03-01")
        active = billing_store.list_admissions(include_discharged=False)
        assert len(active) == 1
        assert active[0].admission_id == a2.admission_id

    def test_discharge(self, billing_store):
        adm = billing_store.create_admission("p1", "X", "2026-01-01")
        discharged = billing_store.discharge(adm.admission_id, "2026-06-30")
        assert not discharged.is_active
        assert discharged.discharge_date == "2026-06-30"

    def test_delete_no_bills(self, billing_store):
        adm = billing_store.create_admission("p1", "X", "2026-01-01")
        billing_store.delete_admission(adm.admission_id)
        assert billing_store.get_admission(adm.admission_id) is None

    def test_delete_with_bills_blocked(self, populated_billing):
        store, adm, bill = populated_billing
        with pytest.raises(AdmissionHasBillsError):
            store.delete_admission(adm.admission_id)

    def test_update_admission(self, billing_store):
        adm = billing_store.create_admission("p1", "X", "2026-01-01", monthly_fee=100)
        updated = billing_store.update_admission(
            adm.admission_id, monthly_fee=5000.0, bed_number="B302",
        )
        assert updated.monthly_fee == 5000.0
        assert updated.bed_number == "B302"

    def test_admission_not_found(self, billing_store):
        with pytest.raises(AdmissionNotFoundError):
            billing_store.update_admission("ghost_id", note="x")


class TestBillCrud:
    def test_create_bill(self, populated_billing):
        store, adm, bill = populated_billing
        assert bill.status == "unpaid"
        assert bill.amount_due == 3800.0
        assert bill.amount_remaining == 3800.0

    def test_duplicate_month_raises(self, populated_billing):
        store, adm, bill = populated_billing
        with pytest.raises(BillExistsError):
            store.create_bill(adm.admission_id, "2026-02", bed_fee=100)

    def test_void_bill(self, billing_store):
        adm = billing_store.create_admission("p1", "X", "2026-01-01")
        bill = billing_store.create_bill(adm.admission_id, "2026-01", bed_fee=100)
        voided = billing_store.void_bill(bill.bill_id, reason="出错")
        assert voided.status == "void"

    def test_void_bill_with_payments_blocked(self, populated_billing):
        store, adm, bill = populated_billing
        store.add_payment(bill.bill_id, 100.0)
        with pytest.raises(Exception):  # BillingError
            store.void_bill(bill.bill_id)

    def test_void_already_voided(self, billing_store):
        adm = billing_store.create_admission("p1", "X", "2026-01-01")
        bill = billing_store.create_bill(adm.admission_id, "2026-01", bed_fee=100)
        billing_store.void_bill(bill.bill_id)
        with pytest.raises(BillVoidedError):
            billing_store.void_bill(bill.bill_id)

    def test_list_bills_by_patient(self, billing_store):
        a1 = billing_store.create_admission("p1", "A", "2026-01-01")
        a2 = billing_store.create_admission("p2", "B", "2026-01-01")
        billing_store.create_bill(a1.admission_id, "2026-01", bed_fee=100)
        billing_store.create_bill(a2.admission_id, "2026-01", bed_fee=200)
        bills = billing_store.list_bills(patient_id="p1")
        assert len(bills) == 1
        assert bills[0].patient_id == "p1"


class TestPaymentStateMachine:
    def test_single_payment_to_paid(self, populated_billing):
        store, adm, bill = populated_billing
        store.add_payment(bill.bill_id, bill.amount_due)
        b = store.get_bill(bill.bill_id)
        assert b.status == "paid"
        assert b.amount_remaining == 0.0

    def test_partial_payment(self, populated_billing):
        store, adm, bill = populated_billing
        store.add_payment(bill.bill_id, 100.0)
        b = store.get_bill(bill.bill_id)
        assert b.status == "partial"
        assert b.amount_paid == 100.0

    def test_overpay_blocked(self, populated_billing):
        store, adm, bill = populated_billing
        with pytest.raises(PaymentExceedsRemainingError):
            store.add_payment(bill.bill_id, bill.amount_due + 0.01)

    def test_void_payment_rolls_back(self, populated_billing):
        store, adm, bill = populated_billing
        p = store.add_payment(bill.bill_id, 1000.0)
        b = store.get_bill(bill.bill_id)
        assert b.status == "partial"
        store.void_payment(p.payment_id, voided_by="admin", reason="test")
        b = store.get_bill(bill.bill_id)
        assert b.amount_paid == 0.0
        assert b.status == "unpaid"

    def test_void_already_voided_payment(self, populated_billing):
        store, adm, bill = populated_billing
        p = store.add_payment(bill.bill_id, 100.0)
        store.void_payment(p.payment_id)
        with pytest.raises(PaymentAlreadyVoidedError):
            store.void_payment(p.payment_id)

    def test_payment_on_voided_bill_blocked(self, billing_store):
        adm = billing_store.create_admission("p1", "X", "2026-01-01")
        bill = billing_store.create_bill(adm.admission_id, "2026-01", bed_fee=100)
        billing_store.void_bill(bill.bill_id)
        with pytest.raises(BillVoidedError):
            billing_store.add_payment(bill.bill_id, 50.0)

    def test_multi_payments_then_partial_void(self, populated_billing):
        store, adm, bill = populated_billing
        p1 = store.add_payment(bill.bill_id, 1000.0, method="wechat")
        p2 = store.add_payment(bill.bill_id, 1000.0, method="cash")
        b = store.get_bill(bill.bill_id)
        assert b.amount_paid == 2000.0
        assert b.status == "partial"
        # void p1 — still partial (p2 remains)
        store.void_payment(p1.payment_id)
        b = store.get_bill(bill.bill_id)
        assert b.amount_paid == 1000.0
        assert b.status == "partial"

    def test_payment_methods(self, populated_billing):
        store, adm, bill = populated_billing
        for m in ("cash", "wechat", "alipay", "bank", "other"):
            p = store.add_payment(bill.bill_id, 1.0, method=m)
            assert p.method == m

    def test_invalid_method(self, populated_billing):
        store, adm, bill = populated_billing
        with pytest.raises(ValueError):
            store.add_payment(bill.bill_id, 1.0, method="bitcoin")


class TestSummary:
    def test_summary_empty(self, billing_store):
        s = billing_store.summary()
        assert s["total_due"] == 0.0
        assert s["total_paid"] == 0.0

    def test_summary_correct(self, populated_billing):
        store, adm, bill = populated_billing
        store.add_payment(bill.bill_id, 1000.0)
        s = store.summary("2026-02")
        assert s["total_due"] == bill.amount_due
        assert s["total_paid"] == 1000.0
        assert s["total_remaining"] == bill.amount_due - 1000.0
        assert s["by_status"]["partial"]["count"] == 1


# =============================================================
# Part B — REST 接口集成测试
# =============================================================
def _build_billing_app(user_store: UserStore, billing_store: BillingStore):
    app = FastAPI()
    app.include_router(billing_router.router, prefix="/api")
    app.add_middleware(AuthTokenMiddleware, legacy_token="", user_store=user_store)
    app.state.user_store = user_store
    app.state.billing_store = billing_store
    app.state.auth_mode = "user_store"

    @app.exception_handler(StarletteHTTPException)
    async def _handler(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": str(exc.detail or "")},
        )

    return app


@pytest.fixture
def billing_client(tmp_path):
    us = UserStore(tmp_path / "users.db")
    bs = BillingStore(tmp_path / "billing.db")

    # admin (has all perms)
    admin = us.create_user("admin", role=BUILTIN_ROLE_ADMIN)
    admin_token, _ = us.create_token(admin.user_id)

    # finance user
    fin = us.create_user("fin_zhang", role=BUILTIN_ROLE_FINANCE)
    fin_token, _ = us.create_token(fin.user_id)

    # nurse (no billing perms)
    nurse = us.create_user("li_nurse", role=BUILTIN_ROLE_NURSE)
    nurse_token, _ = us.create_token(nurse.user_id)

    app = _build_billing_app(us, bs)
    with TestClient(app) as c:
        yield c, admin_token, fin_token, nurse_token


class TestBillingEndpoints:
    def test_nurse_cannot_read_billing(self, billing_client):
        c, _, _, nurse_token = billing_client
        r = c.get("/api/billing/admissions",
                  headers={"X-Auth-Token": nurse_token})
        assert r.status_code == 403
        assert "billing.read" in r.json()["message"]

    def test_nurse_cannot_write_billing(self, billing_client):
        c, _, _, nurse_token = billing_client
        r = c.post("/api/billing/admissions",
                   headers={"X-Auth-Token": nurse_token},
                   json={"patient_id": "p1", "patient_name": "X",
                         "admission_date": "2026-01-01"})
        assert r.status_code == 403

    def test_finance_full_flow(self, billing_client):
        c, _, fin_token, _ = billing_client
        h = {"X-Auth-Token": fin_token}

        # 1. 创建入住
        r = c.post("/api/billing/admissions", headers=h, json={
            "patient_id": "p001", "patient_name": "张三",
            "admission_date": "2026-01-15",
            "bed_number": "A201", "care_level": "二级",
            "monthly_fee": 3800.0,
        })
        assert r.status_code == 200
        adm_id = r.json()["admission"]["admission_id"]

        # 2. 创建账单
        r = c.post("/api/billing/bills", headers=h, json={
            "admission_id": adm_id, "billing_month": "2026-02",
            "bed_fee": 2000.0, "care_fee": 1500.0, "other_fee": 300.0,
        })
        assert r.status_code == 200
        bill_id = r.json()["bill"]["bill_id"]
        assert r.json()["bill"]["status"] == "unpaid"

        # 3. 登记收款（partial）
        r = c.post("/api/billing/payments", headers=h, json={
            "bill_id": bill_id, "amount": 1000.0, "method": "wechat",
        })
        assert r.status_code == 200
        pay_id = r.json()["payment"]["payment_id"]

        # 4. 查看账单 = partial
        r = c.get(f"/api/billing/bills/{bill_id}", headers=h)
        assert r.json()["status"] == "partial"
        assert r.json()["amount_paid"] == 1000.0

        # 5. 付清
        remaining = r.json()["amount_remaining"]
        r = c.post("/api/billing/payments", headers=h, json={
            "bill_id": bill_id, "amount": remaining, "method": "cash",
        })
        assert r.status_code == 200

        r = c.get(f"/api/billing/bills/{bill_id}", headers=h)
        assert r.json()["status"] == "paid"

        # 6. 作废第一笔收款 → 回退到 partial
        r = c.post(f"/api/billing/payments/{pay_id}/void", headers=h,
                   json={"reason": "误收"})
        assert r.status_code == 200
        assert r.json()["is_voided"] is True

        r = c.get(f"/api/billing/bills/{bill_id}", headers=h)
        assert r.json()["status"] == "partial"

        # 7. 汇总
        r = c.get("/api/billing/summary", headers=h,
                  params={"billing_month": "2026-02"})
        assert r.status_code == 200
        assert r.json()["total_due"] > 0

    def test_admin_can_access(self, billing_client):
        c, admin_token, _, _ = billing_client
        r = c.get("/api/billing/admissions",
                  headers={"X-Auth-Token": admin_token})
        assert r.status_code == 200

    def test_overpay_returns_400(self, billing_client):
        c, _, fin_token, _ = billing_client
        h = {"X-Auth-Token": fin_token}
        r = c.post("/api/billing/admissions", headers=h, json={
            "patient_id": "p1", "patient_name": "X",
            "admission_date": "2026-01-01",
        })
        adm_id = r.json()["admission"]["admission_id"]
        r = c.post("/api/billing/bills", headers=h, json={
            "admission_id": adm_id, "billing_month": "2026-01",
            "bed_fee": 100.0,
        })
        bill_id = r.json()["bill"]["bill_id"]
        r = c.post("/api/billing/payments", headers=h, json={
            "bill_id": bill_id, "amount": 999.0,
        })
        assert r.status_code == 400
        assert "超过" in r.json()["message"]

    def test_void_bill_endpoint(self, billing_client):
        c, _, fin_token, _ = billing_client
        h = {"X-Auth-Token": fin_token}
        r = c.post("/api/billing/admissions", headers=h, json={
            "patient_id": "p2", "patient_name": "Y",
            "admission_date": "2026-01-01",
        })
        adm_id = r.json()["admission"]["admission_id"]
        r = c.post("/api/billing/bills", headers=h, json={
            "admission_id": adm_id, "billing_month": "2026-03",
            "bed_fee": 500.0,
        })
        bill_id = r.json()["bill"]["bill_id"]
        r = c.post(f"/api/billing/bills/{bill_id}/void", headers=h,
                   json={"reason": "错开"})
        assert r.status_code == 200
        assert r.json()["status"] == "void"

    def test_discharge_endpoint(self, billing_client):
        c, _, fin_token, _ = billing_client
        h = {"X-Auth-Token": fin_token}
        r = c.post("/api/billing/admissions", headers=h, json={
            "patient_id": "p3", "patient_name": "Z",
            "admission_date": "2026-01-01",
        })
        adm_id = r.json()["admission"]["admission_id"]
        r = c.post(f"/api/billing/admissions/{adm_id}/discharge", headers=h,
                   json={"discharge_date": "2026-06-30"})
        assert r.status_code == 200
        assert r.json()["is_active"] is False

    def test_delete_admission_no_bills(self, billing_client):
        c, _, fin_token, _ = billing_client
        h = {"X-Auth-Token": fin_token}
        r = c.post("/api/billing/admissions", headers=h, json={
            "patient_id": "p4", "patient_name": "W",
            "admission_date": "2026-01-01",
        })
        adm_id = r.json()["admission"]["admission_id"]
        r = c.delete(f"/api/billing/admissions/{adm_id}", headers=h)
        assert r.status_code == 200

    def test_delete_admission_with_bills_409(self, billing_client):
        c, _, fin_token, _ = billing_client
        h = {"X-Auth-Token": fin_token}
        r = c.post("/api/billing/admissions", headers=h, json={
            "patient_id": "p5", "patient_name": "V",
            "admission_date": "2026-01-01",
        })
        adm_id = r.json()["admission"]["admission_id"]
        c.post("/api/billing/bills", headers=h, json={
            "admission_id": adm_id, "billing_month": "2026-01",
            "bed_fee": 100.0,
        })
        r = c.delete(f"/api/billing/admissions/{adm_id}", headers=h)
        assert r.status_code == 409
