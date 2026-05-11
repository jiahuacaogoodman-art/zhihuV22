# -*- coding: utf-8 -*-
"""
Phase 1B 测试：PII 加密字段扩展 + audit diff 泄密修复

Part A  pii_crypto 纯单元测试
          · PII_FIELDS 新扩到 10 个字段
          · is_encryption_enabled() 对密钥/依赖状态的响应
          · encrypt / decrypt 对 10 个字段全部生效
          · 密钥未配置时：encrypt 原样返回，decrypt 对密文返回占位符
          · 双重加密防御：带 enc: 前缀的值不会被再次加密
          · mask_pii_fields 展示层占位符
          · is_ciphertext helper

Part B  audit_log._diff_meta 语义契约
          · PII 字段变化时，输出不含明文也不含密文（纯 mask）
          · 非 PII 字段变化时，输出保留原值（审计可读）
          · 未变化的字段不进 diff

Part C  update_ehr 端到端（防止向量库泄密 + 防止占位符污染）
          · 更新老人床位号时 _build_document 不出现密文前缀
          · 密钥缺失 + 旧数据为密文 + 用户未覆盖该字段 → 503（不静默写回占位符）

注意：
  tests/conftest.py 已 stub 了 chromadb / sentence_transformers 等重依赖，
  所以这里直接 import 业务模块不会崩溃。
"""
from __future__ import annotations

import importlib
import os
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# 一个真正可用的 Fernet 密钥（仅测试用；生产必须 rotate）
_TEST_KEY = "rsnsUTFhD0kHb2TLWGukQ3jV-lGGH0nODWIPGOUOkuA="


@pytest.fixture
def with_encryption_key(monkeypatch):
    """启用加密并强制重新构建 cipher 缓存（_get_cipher 是懒加载）。"""
    monkeypatch.setenv("PII_ENCRYPTION_KEY", _TEST_KEY)
    # pii_crypto._get_cipher() 每次读环境变量，不需要 reload
    yield


@pytest.fixture
def without_encryption_key(monkeypatch):
    monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)
    yield


# =============================================================
# Part A — pii_crypto 单元测试
# =============================================================
class TestPIIFieldsExpansion:
    """Phase 1B 把 PII 字段从 4 个扩到 10 个。"""

    def test_pii_fields_contains_phase1b_additions(self):
        from app.services.pii_crypto import PII_FIELDS
        expected = {
            # Phase 1 已有
            "id_card", "emergency_contact", "emergency_phone", "emergency_relation",
            # Phase 1B 新增
            "name", "birth_date", "bed_number", "primary_nurse", "allergy", "notes",
        }
        assert set(PII_FIELDS) == expected

    def test_is_pii_field(self):
        from app.services.pii_crypto import is_pii_field
        assert is_pii_field("name") is True
        assert is_pii_field("bed_number") is True
        # medical_history 必须保持非 PII（需向量化）
        assert is_pii_field("medical_history") is False
        assert is_pii_field("age") is False


class TestEncryptionToggle:
    def test_is_encryption_enabled_with_valid_key(self, with_encryption_key):
        from app.services.pii_crypto import is_encryption_enabled
        assert is_encryption_enabled() is True

    def test_is_encryption_enabled_without_key(self, without_encryption_key):
        from app.services.pii_crypto import is_encryption_enabled
        assert is_encryption_enabled() is False

    def test_invalid_key_disables_encryption(self, monkeypatch):
        from app.services.pii_crypto import is_encryption_enabled
        monkeypatch.setenv("PII_ENCRYPTION_KEY", "not-a-valid-fernet-key")
        assert is_encryption_enabled() is False


class TestEncryptDecryptRoundtrip:
    def test_all_10_pii_fields_roundtrip(self, with_encryption_key):
        from app.services.pii_crypto import (
            PII_FIELDS, encrypt_pii_fields, decrypt_pii_fields
        )
        plain = {
            "id_card": "110101195001011234",
            "emergency_contact": "张三",
            "emergency_phone": "13800138000",
            "emergency_relation": "子女",
            "name": "王老太",
            "birth_date": "1945-03-21",
            "bed_number": "A-205",
            "primary_nurse": "李护士",
            "allergy": "青霉素",
            "notes": "腿脚不便，需搀扶",
            # 非 PII 字段混进来，应保持不变
            "age": 80,
            "medical_history": "高血压、糖尿病",
        }
        ciphered = encrypt_pii_fields(plain)

        # 10 个 PII 字段都被加密
        for f in PII_FIELDS:
            assert ciphered[f].startswith("enc:"), f"字段 {f} 未被加密"
            assert ciphered[f] != plain[f]

        # 非 PII 原样保留
        assert ciphered["age"] == 80
        assert ciphered["medical_history"] == "高血压、糖尿病"

        # 解密回明文
        decrypted = decrypt_pii_fields(ciphered)
        for f in PII_FIELDS:
            assert decrypted[f] == plain[f], f"{f} 解密不还原"

    def test_encrypt_skips_already_encrypted_value(self, with_encryption_key):
        """双重加密防御：带 enc: 前缀的值应被跳过。"""
        from app.services.pii_crypto import encrypt_pii_fields
        once = encrypt_pii_fields({"name": "王老太"})
        twice = encrypt_pii_fields(once)
        assert once["name"] == twice["name"]  # 未再次加密

    def test_empty_and_none_values_ignored(self, with_encryption_key):
        from app.services.pii_crypto import encrypt_pii_fields
        out = encrypt_pii_fields({"name": "", "id_card": None, "bed_number": "A1"})
        assert out["name"] == ""
        assert out["id_card"] is None
        assert out["bed_number"].startswith("enc:")

    def test_encrypt_returns_new_dict_not_mutate(self, with_encryption_key):
        from app.services.pii_crypto import encrypt_pii_fields
        src = {"name": "王老太"}
        out = encrypt_pii_fields(src)
        assert src["name"] == "王老太"  # 原对象未变
        assert out["name"].startswith("enc:")


class TestKeyMissingBehavior:
    def test_encrypt_without_key_returns_same_dict(self, without_encryption_key):
        from app.services.pii_crypto import encrypt_pii_fields
        src = {"name": "王老太", "id_card": "110101"}
        out = encrypt_pii_fields(src)
        # 密钥未配置：原样返回（非 placeholder）
        assert out["name"] == "王老太"
        assert out["id_card"] == "110101"

    def test_decrypt_without_key_masks_existing_ciphertext(self, without_encryption_key):
        """最危险的场景：之前加密过的数据，运维把密钥弄丢了。
        必须：1) 不泄露密文给响应方；2) 用占位符表达'无法读取'。"""
        from app.services.pii_crypto import decrypt_pii_fields
        src = {"name": "enc:gAAAAABfakecipher", "age": 80}
        out = decrypt_pii_fields(src)
        assert out["name"].startswith("[加密数据-")
        assert "enc:" not in out["name"]
        # 非 PII 不受影响
        assert out["age"] == 80

    def test_decrypt_without_key_passes_plaintext_through(self, without_encryption_key):
        """没加密过的旧数据（不带 enc: 前缀）在无密钥时应原样返回。"""
        from app.services.pii_crypto import decrypt_pii_fields
        out = decrypt_pii_fields({"name": "王老太", "age": 80})
        assert out == {"name": "王老太", "age": 80}

    def test_decrypt_with_wrong_key_returns_mask(self, monkeypatch):
        from app.services.pii_crypto import encrypt_pii_fields, decrypt_pii_fields
        from cryptography.fernet import Fernet

        # 用 key A 加密
        key_a = Fernet.generate_key().decode()
        monkeypatch.setenv("PII_ENCRYPTION_KEY", key_a)
        ciphered = encrypt_pii_fields({"name": "王老太"})
        assert ciphered["name"].startswith("enc:")

        # 切换到 key B 解密
        key_b = Fernet.generate_key().decode()
        monkeypatch.setenv("PII_ENCRYPTION_KEY", key_b)
        out = decrypt_pii_fields(ciphered)
        assert out["name"] == "[解密失败]"


class TestMaskAndHelpers:
    def test_mask_pii_fields(self):
        from app.services.pii_crypto import mask_pii_fields
        src = {
            "name": "王老太", "bed_number": "A1",
            "age": 80, "medical_history": "糖尿病",
        }
        out = mask_pii_fields(src)
        assert out["name"] == "[已加密]"
        assert out["bed_number"] == "[已加密]"
        # 非 PII 不动
        assert out["age"] == 80
        assert out["medical_history"] == "糖尿病"

    def test_is_ciphertext(self):
        from app.services.pii_crypto import is_ciphertext
        assert is_ciphertext("enc:abc") is True
        assert is_ciphertext("王老太") is False
        assert is_ciphertext("") is False
        assert is_ciphertext(None) is False
        assert is_ciphertext(123) is False


# =============================================================
# Part B — audit_log._diff_meta 语义契约
# =============================================================
class TestDiffMetaContract:
    def test_pii_change_masks_value(self):
        """PII 字段有变化 → 输出 [已加密] 占位符，不含明文。"""
        from app.services.audit_log import _diff_meta
        before = {"name": "王老太", "id_card": "110101", "age": 80}
        after = {"name": "王老太太", "id_card": "110102", "age": 80}
        diff = _diff_meta(before, after, ["name", "id_card", "age"])

        assert diff["before"]["name"] == "[已加密]"
        assert diff["after"]["name"] == "[已加密]"
        assert diff["before"]["id_card"] == "[已加密]"
        # 没有明文泄露
        assert "王老太" not in str(diff)
        assert "110101" not in str(diff)
        # age 没变化，不应出现
        assert "age" not in diff["before"]

    def test_non_pii_change_keeps_value(self):
        """床位号以前是非 PII，现在是 PII;用 age 这个纯非 PII 字段验证保真度。"""
        from app.services.audit_log import _diff_meta
        before = {"age": 80, "care_level": "一级"}
        after = {"age": 81, "care_level": "二级"}
        diff = _diff_meta(before, after, ["age", "care_level"])
        assert diff["before"] == {"age": 80, "care_level": "一级"}
        assert diff["after"] == {"age": 81, "care_level": "二级"}

    def test_no_change_returns_empty(self):
        from app.services.audit_log import _diff_meta
        diff = _diff_meta(
            {"name": "王老太", "age": 80},
            {"name": "王老太", "age": 80},
            ["name", "age"],
        )
        assert diff == {}

    def test_ciphertext_passed_still_masked(self):
        """契约要求调用方传明文，但即使错传密文，也不应该把密文写进 diff。"""
        from app.services.audit_log import _diff_meta
        diff = _diff_meta(
            {"name": "enc:gAAAAAold"},
            {"name": "enc:gAAAAAnew"},
            ["name"],
        )
        # 两边都是 PII 字段 → 都 mask
        assert diff["before"]["name"] == "[已加密]"
        assert diff["after"]["name"] == "[已加密]"
        assert "enc:" not in str(diff)


# =============================================================
# Part C — update_ehr 端到端
# =============================================================
class _StubCollection:
    """最小 Chroma collection stub，记录被写入的 document/metadata 供断言。"""

    def __init__(self, initial_meta: dict, initial_doc: str):
        self._initial_meta = initial_meta
        self._initial_doc = initial_doc
        self.write_calls = []  # [(doc_id, document, metadata)]

    def get(self, where=None, ids=None, include=None):
        # update_ehr 调用：where=patient_id → 返回现有老数据
        return {
            "ids": ["p_doc_old"],
            "documents": [self._initial_doc],
            "metadatas": [self._initial_meta],
        }

    def add(self, ids, documents, embeddings, metadatas):
        self.write_calls.append((ids[0], documents[0], metadatas[0]))

    def delete(self, ids=None):
        pass


def _build_ehr_app_with_token(user_store, admin_token, stub_col):
    """拼一个只挂 ehr 路由的 mini app，方便断言写入行为。"""
    from app.middleware.auth import AuthTokenMiddleware
    from app.routers import ehr

    embedding_fn = MagicMock()
    embedding_fn.encode.return_value = MagicMock(tolist=lambda: [0.0] * 8)

    app = FastAPI()
    app.include_router(ehr.router, prefix="/api")
    app.add_middleware(AuthTokenMiddleware, legacy_token="", user_store=user_store)
    app.state.user_store = user_store
    app.state.auth_mode = "user_store"

    ehr._get_state_backup = ehr._get_state
    ehr._get_state = lambda: (stub_col, embedding_fn)
    # 同时 stub audit.log 以免 SQLite 副作用
    ehr._audit_backup = ehr.audit.log
    ehr.audit.log = lambda *a, **kw: None
    return app, ehr


def _restore_ehr(ehr_mod):
    ehr_mod._get_state = ehr_mod._get_state_backup
    ehr_mod.audit.log = ehr_mod._audit_backup


class TestUpdateEhrVectorDocNoLeaks:
    def test_update_does_not_write_ciphertext_into_document(
        self, tmp_path, with_encryption_key
    ):
        """
        真实泄密路径回归：
          · 老数据 meta 是密文（正常，写 ChromaDB 前会加密）
          · 用户更新床位号（bed_number 现在是 PII）
          · 新 document 文本要被向量化——不能含 enc: 密文前缀
        """
        from app.services.pii_crypto import encrypt_pii_fields
        from app.services.user_store import UserStore

        # 准备 admin token
        store = UserStore(tmp_path / "users.db")
        admin = store.create_user("admin", role="admin")
        token, _ = store.create_token(admin.user_id)

        # 准备老的 meta：PII 字段都是密文（模拟 ChromaDB 已存的数据）
        old_plain = {
            "patient_id": "p001",
            "name": "王老太",
            "age": 80,
            "bed_number": "A-205",
            "primary_nurse": "李护士",
            "medical_history": "高血压、糖尿病",
            "doc_type": "patient_profile",
        }
        old_meta_in_db = encrypt_pii_fields(old_plain)
        # 验证 fixture 正确：PII 字段确实已加密
        assert old_meta_in_db["name"].startswith("enc:")
        assert old_meta_in_db["bed_number"].startswith("enc:")

        old_doc_in_db = "患者姓名：王老太；床位号：A-205"
        stub_col = _StubCollection(old_meta_in_db, old_doc_in_db)

        app, ehr_mod = _build_ehr_app_with_token(store, token, stub_col)
        try:
            with TestClient(app) as c:
                r = c.put(
                    "/api/ehr/patients/p001",
                    headers={"X-Auth-Token": token},
                    json={"bed_number": "B-310"},
                )
            assert r.status_code == 200, r.text
            assert len(stub_col.write_calls) == 1
            _, new_document, new_metadata = stub_col.write_calls[0]

            # 核心断言：向量化文本里不能有任何 enc: 密文
            assert "enc:" not in new_document, (
                f"泄密：新 document 含密文前缀: {new_document!r}"
            )
            # 新 document 里应该是明文床位号
            assert "B-310" in new_document
            # metadata 回写时 PII 被再次加密（enc: 前缀）
            assert new_metadata["bed_number"].startswith("enc:")
            assert new_metadata["name"].startswith("enc:")
        finally:
            _restore_ehr(ehr_mod)

    def test_update_rejects_when_untouched_pii_cannot_decrypt(self, tmp_path, monkeypatch):
        """
        防御场景：
          · 旧数据里有密文（以前配过 key_A 加密的）
          · 现在实例启动时换了 key_B（或 key 丢了）
          · 用户更新其他字段（bed_number），没传 name
          · 我们绝不能把 "[加密数据-需配置...]" 占位符写回 name 字段
          · 必须 503 中止
        """
        from app.services.user_store import UserStore

        store = UserStore(tmp_path / "users.db")
        admin = store.create_user("admin", role="admin")
        token, _ = store.create_token(admin.user_id)

        # 手工构造"看似密文但本实例无法解密"的旧数据
        # 简单办法：让 decrypt_pii_fields 看到 enc: 前缀但密钥是空的
        old_meta_in_db = {
            "patient_id": "p002",
            "name": "enc:gAAAAABfakecipher_with_wrong_key",
            "age": 80,
            "bed_number": "enc:gAAAAABfakecipher_bed",
            "medical_history": "高血压",
            "doc_type": "patient_profile",
        }
        stub_col = _StubCollection(old_meta_in_db, "old doc")

        # 密钥清空 → decrypt 会把密文转成占位符
        monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)

        app, ehr_mod = _build_ehr_app_with_token(store, token, stub_col)
        try:
            with TestClient(app) as c:
                r = c.put(
                    "/api/ehr/patients/p002",
                    headers={"X-Auth-Token": token},
                    json={"age": 81},  # 只改非 PII，name 被占位符污染
                )
            # 应该被防御拒绝
            assert r.status_code == 503, r.text
            # 没有任何写入发生
            assert len(stub_col.write_calls) == 0
        finally:
            _restore_ehr(ehr_mod)

    def test_update_allowed_when_user_overwrites_masked_pii(self, tmp_path, monkeypatch):
        """
        与上一个对比：如果用户本次请求里显式覆盖了那个无法解密的字段，
        防御机制应该放行（因为新值会覆盖占位符）。
        """
        from app.services.user_store import UserStore

        store = UserStore(tmp_path / "users.db")
        admin = store.create_user("admin", role="admin")
        token, _ = store.create_token(admin.user_id)

        # 只 name 字段无法解密；其他 PII 字段在 meta 里根本不存在
        old_meta_in_db = {
            "patient_id": "p003",
            "name": "enc:gAAAAABfakecipher_unknown_key",
            "age": 80,
            "medical_history": "高血压",
            "doc_type": "patient_profile",
        }
        stub_col = _StubCollection(old_meta_in_db, "old doc")
        monkeypatch.delenv("PII_ENCRYPTION_KEY", raising=False)

        app, ehr_mod = _build_ehr_app_with_token(store, token, stub_col)
        try:
            with TestClient(app) as c:
                # 用户主动覆盖 name —— 占位符被替换，应该 200
                r = c.put(
                    "/api/ehr/patients/p003",
                    headers={"X-Auth-Token": token},
                    json={"name": "王老太", "age": 81},
                )
            assert r.status_code == 200, r.text
            _, new_document, new_metadata = stub_col.write_calls[0]
            # 新的 document 必然含"王老太"且不含占位符或密文
            assert "王老太" in new_document
            assert "[加密数据" not in new_document
            assert "enc:" not in new_document
        finally:
            _restore_ehr(ehr_mod)


class TestHealthExposesPIIStatus:
    """/health 应当把 pii_encryption_enabled 状态暴露出来。"""

    def test_health_field_present(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert "pii_encryption_enabled" in body
        assert isinstance(body["pii_encryption_enabled"], bool)
        assert "auth_mode" in body
