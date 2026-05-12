# -*- coding: utf-8 -*-
"""
@File    : routers/ehr.py
@Desc    : EHR 档案管理路由：患者基本档案 + 病历照片上传 + 本地 OCR + 向量化检索入库
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile, status
from loguru import logger

from app.core.config import EHR_UPLOAD_DIR, MAX_UPLOAD_SIZE_MB, ALLOWED_UPLOAD_EXTENSIONS, BASE_DIR
from app.middleware.auth import get_current_user, require_permission
from app.services.audit_log import get_audit_log, _diff_meta
from app.services.permissions import PERM_EHR_AUDIT_READ
from app.services.pii_crypto import PII_FIELDS, encrypt_pii_fields, decrypt_pii_fields
from app.services.user_store import User
from app.models.schemas import (
    EHRAddRequest, EHRAddResponse,
    EHRUpdateRequest, EHRUpdateResponse,
    EHRDeleteRequest, EHRDeleteResponse,
    EHRListResponse, EHRRecord
)
from app.services.ocr_service import LocalOCRService

router = APIRouter()
ocr_service = LocalOCRService()

# 审计日志：全局 singleton，ehr / nursing / ReadAuditMiddleware 共用同一实例
# 避免多个 sqlite3 连接池争用 WAL 文件，也让测试更容易 reset。
audit = get_audit_log()

UPLOAD_ROOT = Path(EHR_UPLOAD_DIR)
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

# 所有可存入 metadata 的字段列表（ChromaDB metadata 只支持 str/int/float/bool）
META_FIELDS = [
    "patient_id", "name", "age", "gender", "birth_date", "id_card",
    "admission_date", "emergency_contact", "emergency_phone", "emergency_relation",
    "height_cm", "weight_kg", "blood_type", "care_level", "bed_number",
    "primary_nurse", "medical_history", "allergy", "diet_restriction", "notes", "doc_type",
    "record_type", "original_filename", "stored_filename", "file_path", "file_url",
    "ocr_text_path", "ocr_status", "ocr_engine", "ocr_error", "uploaded_at",
    "content_type", "file_size", "manual_text"
]

PROFILE_DOC_TYPE = "patient_profile"
UPLOAD_DOC_TYPE = "medical_record_upload"


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


def _safe_filename(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in {".", "_", "-"}:
            keep.append(ch)
        else:
            keep.append("_")
    cleaned = "".join(keep).strip("._")
    return cleaned or "medical_record"


def _patient_upload_dir(patient_id: str) -> Path:
    safe_pid = _safe_filename(patient_id)
    root = UPLOAD_ROOT / safe_pid
    (root / "photos").mkdir(parents=True, exist_ok=True)
    (root / "ocr").mkdir(parents=True, exist_ok=True)
    return root


def _build_metadata(payload_dict: dict) -> dict:
    """从请求字典中提取非空字段，构建 ChromaDB metadata（只允许基础类型）。
    高敏感 PII 字段（身份证、紧急联系人电话等）在写入前透明加密。
    """
    meta = {}
    for field in META_FIELDS:
        val = payload_dict.get(field)
        if val is not None:
            meta[field] = val if isinstance(val, (str, int, float, bool)) else str(val)
    # PII 加密：加密后的密文仍是字符串，对 ChromaDB metadata 类型无影响
    return encrypt_pii_fields(meta)


def _build_document(payload_dict: dict) -> str:
    """
    将档案信息拼接为可向量化的文本，用于 Embedding 检索。
    包含所有非空字段，使大模型检索时能匹配更多上下文。
    """
    parts = []
    name = payload_dict.get("name", "")
    pid = payload_dict.get("patient_id", "")
    parts.append(f"患者姓名：{name}，编号：{pid}")

    if payload_dict.get("age"):
        parts.append(f"年龄：{payload_dict['age']}岁")
    if payload_dict.get("gender"):
        parts.append(f"性别：{payload_dict['gender']}")
    if payload_dict.get("birth_date"):
        parts.append(f"出生日期：{payload_dict['birth_date']}")
    if payload_dict.get("blood_type"):
        parts.append(f"血型：{payload_dict['blood_type']}")
    if payload_dict.get("height_cm") or payload_dict.get("weight_kg"):
        h = payload_dict.get("height_cm", "—")
        w = payload_dict.get("weight_kg", "—")
        parts.append(f"身高：{h}cm，体重：{w}kg")
    if payload_dict.get("care_level"):
        parts.append(f"护理等级：{payload_dict['care_level']}")
    if payload_dict.get("bed_number"):
        parts.append(f"床位号：{payload_dict['bed_number']}")
    if payload_dict.get("primary_nurse"):
        parts.append(f"主管护工：{payload_dict['primary_nurse']}")
    if payload_dict.get("allergy"):
        parts.append(f"过敏史：{payload_dict['allergy']}")
    if payload_dict.get("diet_restriction"):
        parts.append(f"饮食禁忌：{payload_dict['diet_restriction']}")
    if payload_dict.get("medical_history"):
        parts.append(f"既往病史及用药：{payload_dict['medical_history']}")
    if payload_dict.get("emergency_contact"):
        ec = payload_dict.get("emergency_contact", "")
        ep = payload_dict.get("emergency_phone", "")
        er = payload_dict.get("emergency_relation", "")
        parts.append(f"紧急联系人：{ec}（{er}），电话：{ep}")
    if payload_dict.get("notes"):
        parts.append(f"备注：{payload_dict['notes']}")

    return "；".join(parts)


def _build_upload_document(meta: dict, ocr_text: str, manual_text: Optional[str] = None) -> str:
    """病历照片 OCR 文本入向量库，供后续 RAG 检索。"""
    parts = [
        f"【病历照片OCR档案】患者姓名：{meta.get('name', '')}，编号：{meta.get('patient_id', '')}",
        f"病历类型：{meta.get('record_type', '未分类')}",
        f"原始文件：{meta.get('original_filename', '')}",
        f"上传时间：{meta.get('uploaded_at', '')}",
    ]
    if meta.get("notes"):
        parts.append(f"备注：{meta.get('notes')}")
    if ocr_text.strip():
        parts.append("OCR识别文本：\n" + ocr_text.strip())
    else:
        parts.append("OCR识别文本：未识别到有效文字或本地OCR引擎未配置")
    if manual_text and manual_text.strip():
        parts.append("人工补充/校正文书：\n" + manual_text.strip())
    return "\n".join(parts)


def _meta_to_record(doc_id: str, document: str, meta: dict) -> EHRRecord:
    """将 ChromaDB metadata 还原为 EHRRecord 对象（PII 字段透明解密）"""
    meta = decrypt_pii_fields(meta)
    return EHRRecord(
        doc_id=doc_id,
        patient_id=meta.get("patient_id", ""),
        name=meta.get("name", ""),
        age=int(meta["age"]) if meta.get("age") is not None else None,
        gender=meta.get("gender"),
        birth_date=meta.get("birth_date"),
        id_card=meta.get("id_card"),
        admission_date=meta.get("admission_date"),
        emergency_contact=meta.get("emergency_contact"),
        emergency_phone=meta.get("emergency_phone"),
        emergency_relation=meta.get("emergency_relation"),
        height_cm=float(meta["height_cm"]) if meta.get("height_cm") is not None else None,
        weight_kg=float(meta["weight_kg"]) if meta.get("weight_kg") is not None else None,
        blood_type=meta.get("blood_type"),
        care_level=meta.get("care_level"),
        bed_number=meta.get("bed_number"),
        primary_nurse=meta.get("primary_nurse"),
        medical_history=meta.get("medical_history") or document,
        allergy=meta.get("allergy"),
        diet_restriction=meta.get("diet_restriction"),
        notes=meta.get("notes"),
    )


def _is_profile(meta: dict) -> bool:
    # 兼容旧数据：旧版本没有 doc_type，默认视为患者基本档案。
    return meta.get("doc_type") in (None, "", PROFILE_DOC_TYPE)


def _is_upload(meta: dict) -> bool:
    return meta.get("doc_type") == UPLOAD_DOC_TYPE


def _find_patient_name(collection, patient_id: str) -> Optional[str]:
    result = collection.get(where={"patient_id": {"$eq": patient_id}}, include=["metadatas"])
    for meta in result.get("metadatas", []):
        if _is_profile(meta) and meta.get("name"):
            return meta.get("name")
    for meta in result.get("metadatas", []):
        if meta.get("name"):
            return meta.get("name")
    return None


def _add_document_to_collection(collection, embedding_function, doc_id: str, document: str, metadata: dict) -> None:
    embedding_vector = embedding_function.encode(document).tolist()
    collection.add(ids=[doc_id], documents=[document], embeddings=[embedding_vector], metadatas=[metadata])


# ── 旧版兼容：录入档案 ───────────────────────────────────────────
@router.post("/ehr/add", response_model=EHRAddResponse, summary="录入老人 EHR 档案")
async def add_ehr(payload: EHRAddRequest, user: User = Depends(get_current_user)):
    collection, embedding_function = _get_state()
    doc_id = f"{payload.patient_id}_{uuid.uuid4().hex[:8]}"
    logger.info(f"录入档案: patient_id={payload.patient_id}, doc_id={doc_id}, operator={user.username}")

    payload_dict = payload.model_dump()
    payload_dict["doc_type"] = PROFILE_DOC_TYPE
    document = _build_document(payload_dict)
    metadata = _build_metadata(payload_dict)

    try:
        _add_document_to_collection(collection, embedding_function, doc_id, document, metadata)
        logger.success(f"档案录入成功: doc_id={doc_id}")
        audit.log("PATIENT_CREATE", payload.patient_id, user.username,
                  doc_id=doc_id, detail=f"新建患者档案: {payload.name}")
        return EHRAddResponse(
            code=200,
            message=f"患者 {payload.name} 的档案已成功录入",
            patient_id=payload.patient_id,
            doc_id=doc_id
        )
    except Exception as e:
        logger.error(f"档案录入失败: {e}")
        raise HTTPException(status_code=500, detail=f"录入失败: {str(e)}")


# ── 新版 REST：前端页面使用 ──────────────────────────────────────
@router.post("/ehr/patients", summary="新增患者基本档案")
async def create_patient(payload: EHRAddRequest, user: User = Depends(get_current_user)):
    return await add_ehr(payload, user=user)


@router.get("/ehr/patients", summary="查询患者基本档案列表")
async def list_patients(user: User = Depends(get_current_user)):
    collection, _ = _get_state()
    result = collection.get(include=["documents", "metadatas"])
    records = []
    seen = set()
    for doc_id, doc, meta in zip(result.get("ids", []), result.get("documents", []), result.get("metadatas", [])):
        if not _is_profile(meta):
            continue
        pid = meta.get("patient_id", "")
        if pid in seen:
            continue
        seen.add(pid)
        records.append(_meta_to_record(doc_id, doc, meta).model_dump())
    # 只读审计：列表查询记 PATIENT_LIST，patient_id 留空，detail 里写条数
    audit.log("PATIENT_LIST", "", user.username,
              detail=f"查询患者列表，共返回 {len(records)} 条")
    return records


@router.get("/ehr/patients/{patient_id}", summary="查询单个患者基本档案")
async def get_patient(patient_id: str, user: User = Depends(get_current_user)):
    collection, _ = _get_state()
    result = collection.get(where={"patient_id": {"$eq": patient_id}}, include=["documents", "metadatas"])
    for doc_id, doc, meta in zip(result.get("ids", []), result.get("documents", []), result.get("metadatas", [])):
        if _is_profile(meta):
            record = _meta_to_record(doc_id, doc, meta).model_dump()
            # 只读审计：查看成功才记录（404 分支不记）
            audit.log("PATIENT_READ", patient_id, user.username,
                      doc_id=doc_id, detail=f"查看患者基本档案: {record.get('name', '')} (来源=ehr)")
            return record
    raise HTTPException(status_code=404, detail=f"未找到 patient_id='{patient_id}' 的患者基本档案")


@router.put("/ehr/patients/{patient_id}", summary="修改患者基本档案")
async def update_patient(
    patient_id: str,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
):
    payload["patient_id"] = patient_id
    req = EHRUpdateRequest(**payload)
    return await update_ehr(req, user=user)


@router.delete("/ehr/patients/{patient_id}", summary="删除患者全部档案")
async def delete_patient(patient_id: str, user: User = Depends(get_current_user)):
    return await delete_ehr(EHRDeleteRequest(patient_id=patient_id), user=user)


# ── 旧版兼容：查询所有基本档案 ───────────────────────────────────
@router.get("/ehr/list", response_model=EHRListResponse, summary="查询所有已录入档案")
async def list_ehr(user: User = Depends(get_current_user)):
    records = [EHRRecord(**item) for item in await list_patients(user=user)]
    logger.info(f"查询档案列表，共 {len(records)} 条")
    return EHRListResponse(code=200, total=len(records), records=records)


# ── 旧版兼容：修改基本档案 ───────────────────────────────────────
@router.post("/ehr/update", response_model=EHRUpdateResponse, summary="修改患者档案")
async def update_ehr(payload: EHRUpdateRequest, user: User = Depends(get_current_user)):
    collection, embedding_function = _get_state()
    try:
        result = collection.get(where={"patient_id": {"$eq": payload.patient_id}}, include=["documents", "metadatas"])
        existing_ids = []
        old_meta = {}
        old_doc = ""
        for doc_id, doc, meta in zip(result.get("ids", []), result.get("documents", []), result.get("metadatas", [])):
            if _is_profile(meta):
                existing_ids.append(doc_id)
                if not old_meta:
                    old_meta = meta or {}
                    old_doc = doc or ""

        if not existing_ids:
            raise HTTPException(status_code=404, detail=f"未找到 patient_id='{payload.patient_id}' 的基本档案")

        # 1) 解密旧 meta，让后续 merge / diff / document 构建全程走明文。
        #    Phase 1B 修复：此前 merged = dict(old_meta) 会把密文直接带入，导致：
        #      · _build_document 生成含密文的向量化文本（向量库泄密）
        #      · _diff_meta 对密文做比较（审计日志泄密）
        old_meta_plain = decrypt_pii_fields(old_meta)

        # 2) 防御：若某 PII 字段在旧数据里是密文但本实例无法解密（密钥缺失/轮换中），
        #    decrypt 会返回占位符字符串。把占位符当明文写回会污染数据，直接 500。
        #    （此检查仅对用户未主动覆盖的字段生效——如果用户在本次请求里显式传了
        #     name=xxx，后面的 merge 会覆盖占位符，无需中止。）
        new_data = payload.model_dump(exclude_unset=True)
        new_data.pop("patient_id", None)
        _mask_prefixes = ("[加密数据-需配置", "[解密失败")
        untouched_masked = [
            f for f in PII_FIELDS
            if f not in new_data
            and isinstance(old_meta_plain.get(f), str)
            and old_meta_plain[f].startswith(_mask_prefixes)
        ]
        if untouched_masked:
            logger.error(
                f"拒绝修改 patient_id={payload.patient_id}：字段 {untouched_masked} "
                f"无法解密（可能 PII_ENCRYPTION_KEY 未正确配置），避免把占位符写回数据库"
            )
            raise HTTPException(
                status_code=503,
                detail="PII 字段无法解密（请检查 PII_ENCRYPTION_KEY 配置），修改已中止",
            )

        merged = dict(old_meta_plain)
        # 从旧文档中保底继承 medical_history，避免编辑其他字段时病史丢失。
        merged.setdefault("medical_history", old_doc)
        for k, v in new_data.items():
            if v is not None:
                merged[k] = v

        merged["patient_id"] = payload.patient_id
        merged["doc_type"] = PROFILE_DOC_TYPE
        new_document = _build_document(merged)        # 明文 → 可向量化检索
        new_metadata = _build_metadata(merged)        # 内部会 encrypt_pii_fields

        collection.delete(ids=existing_ids)
        new_doc_id = f"{payload.patient_id}_{uuid.uuid4().hex[:8]}"
        _add_document_to_collection(collection, embedding_function, new_doc_id, new_document, new_metadata)
        logger.success(f"档案修改成功: patient_id={payload.patient_id}, operator={user.username}")
        # _diff_meta 约定输入明文，PII 字段内部做 mask
        diff = _diff_meta(old_meta_plain, merged, list(merged.keys()))
        audit.log("PATIENT_UPDATE", payload.patient_id, user.username,
                  doc_id=new_doc_id, detail=f"修改患者档案: {merged.get('name', '')}",
                  diff=diff)
        return EHRUpdateResponse(
            code=200,
            message=f"患者 {merged.get('name', payload.patient_id)} 的基本档案已更新",
            patient_id=payload.patient_id,
            updated_count=len(existing_ids)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"档案修改失败: {e}")
        raise HTTPException(status_code=500, detail=f"修改失败: {str(e)}")


# ── 病历照片上传 + OCR + 同步入库 ────────────────────────────────
@router.post("/ehr/records/upload", summary="上传病历照片并进行本地 OCR 识别")
async def upload_medical_records(
    patient_id: str = Form(...),
    name: Optional[str] = Form(None),
    record_type: str = Form("病历档案"),
    notes: Optional[str] = Form(None),
    manual_text: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
    user: User = Depends(get_current_user),
):
    collection, embedding_function = _get_state()
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一张病历照片")

    patient_name = name or _find_patient_name(collection, patient_id) or ""
    root = _patient_upload_dir(patient_id)
    saved = []
    max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024

    for file in files:
        original_name = file.filename or "medical_record.jpg"
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型：{suffix}，请上传图片文件")

        content = await file.read()
        if len(content) > max_bytes:
            raise HTTPException(status_code=413, detail=f"单个文件不能超过 {MAX_UPLOAD_SIZE_MB}MB")

        doc_id = f"{patient_id}_record_{uuid.uuid4().hex[:10]}"
        stored_name = f"{doc_id}{suffix}"
        photo_path = root / "photos" / stored_name
        with open(photo_path, "wb") as f:
            f.write(content)

        ocr = ocr_service.extract_text(photo_path)
        uploaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ocr_txt_name = f"{doc_id}.txt"
        ocr_txt_path = root / "ocr" / ocr_txt_name
        ocr_txt_path.write_text(ocr.text or "", encoding="utf-8")

        rel_file_url = f"/uploads/{_safe_filename(patient_id)}/photos/{stored_name}"
        meta = {
            "patient_id": patient_id,
            "name": patient_name,
            "doc_type": UPLOAD_DOC_TYPE,
            "record_type": record_type,
            "notes": notes,
            "manual_text": manual_text,
            "original_filename": original_name,
            "stored_filename": stored_name,
            "file_path": str(photo_path),
            "file_url": rel_file_url,
            "ocr_text_path": str(ocr_txt_path),
            "ocr_status": ocr.status,
            "ocr_engine": ocr.engine,
            "ocr_error": ocr.error,
            "uploaded_at": uploaded_at,
            "content_type": file.content_type or "image/*",
            "file_size": len(content),
        }
        document = _build_upload_document(meta, ocr.text, manual_text=manual_text)
        metadata = _build_metadata(meta)
        try:
            _add_document_to_collection(collection, embedding_function, doc_id, document, metadata)
        except Exception:
            # 保持事务性：向量库写失败则删除已保存文件。
            photo_path.unlink(missing_ok=True)
            ocr_txt_path.unlink(missing_ok=True)
            raise

        saved.append({
            "doc_id": doc_id,
            "patient_id": patient_id,
            "name": patient_name,
            "record_type": record_type,
            "original_filename": original_name,
            "file_url": rel_file_url,
            "ocr_text": ocr.text,
            "ocr_status": ocr.status,
            "ocr_engine": ocr.engine,
            "ocr_error": ocr.error,
            "uploaded_at": uploaded_at,
        })

    logger.success(f"病历照片上传完成: patient_id={patient_id}, count={len(saved)}, operator={user.username}")
    for rec in saved:
        audit.log("RECORD_UPLOAD", patient_id, user.username,
                  doc_id=rec["doc_id"],
                  detail=f"上传病历照片: {rec['original_filename']} (ocr={rec['ocr_status']})")
    return {"code": 200, "message": f"已上传 {len(saved)} 份病历照片，并同步保存原图与 OCR 文本", "records": saved}


@router.get("/ehr/records/{patient_id}", summary="查询某患者的病历照片与 OCR 文本")
async def list_medical_records(patient_id: str, user: User = Depends(get_current_user)):
    collection, _ = _get_state()
    result = collection.get(where={"patient_id": {"$eq": patient_id}}, include=["documents", "metadatas"])
    records = []
    for doc_id, doc, meta in zip(result.get("ids", []), result.get("documents", []), result.get("metadatas", [])):
        if not _is_upload(meta):
            continue
        ocr_text = ""
        p = meta.get("ocr_text_path")
        if p and Path(p).exists():
            ocr_text = Path(p).read_text(encoding="utf-8", errors="ignore")
        else:
            # 兼容：从向量文档中也能看到 OCR 文本。
            ocr_text = doc or ""
        records.append({
            "doc_id": doc_id,
            "patient_id": meta.get("patient_id"),
            "name": meta.get("name"),
            "record_type": meta.get("record_type"),
            "notes": meta.get("notes"),
            "original_filename": meta.get("original_filename"),
            "file_url": meta.get("file_url"),
            "ocr_status": meta.get("ocr_status"),
            "ocr_engine": meta.get("ocr_engine"),
            "ocr_error": meta.get("ocr_error"),
            "ocr_text": ocr_text,
            "manual_text": meta.get("manual_text"),
            "uploaded_at": meta.get("uploaded_at"),
            "file_size": meta.get("file_size"),
        })
    records.sort(key=lambda x: x.get("uploaded_at") or "", reverse=True)
    # 只读审计：查询病历列表
    audit.log("RECORD_READ", patient_id, user.username,
              detail=f"查询病历照片列表，共 {len(records)} 份")
    return {"code": 200, "patient_id": patient_id, "total": len(records), "records": records}


@router.delete("/ehr/records/{doc_id}", summary="删除单份病历照片档案")
async def delete_medical_record(doc_id: str, user: User = Depends(get_current_user)):
    collection, _ = _get_state()
    result = collection.get(ids=[doc_id], include=["metadatas"])
    ids = result.get("ids", [])
    if not ids:
        raise HTTPException(status_code=404, detail="未找到该病历照片档案")
    meta = result.get("metadatas", [{}])[0] or {}
    if not _is_upload(meta):
        raise HTTPException(status_code=400, detail="该 doc_id 不是病历照片档案，不能通过此接口删除")

    for key in ["file_path", "ocr_text_path"]:
        p = meta.get(key)
        if p:
            Path(p).unlink(missing_ok=True)
    collection.delete(ids=[doc_id])
    audit.log("RECORD_DELETE", meta.get("patient_id", ""), user.username,
              doc_id=doc_id,
              detail=f"删除病历照片: {meta.get('original_filename', '')}")
    return {"code": 200, "message": "病历照片档案已删除", "doc_id": doc_id}


# ── 旧版兼容：删除患者全部档案 ───────────────────────────────────
@router.post("/ehr/delete", response_model=EHRDeleteResponse, summary="删除患者档案")
async def delete_ehr(payload: EHRDeleteRequest, user: User = Depends(get_current_user)):
    collection, _ = _get_state()
    try:
        result = collection.get(where={"patient_id": {"$eq": payload.patient_id}}, include=["metadatas"])
        existing_ids = result.get("ids", [])
        if not existing_ids:
            raise HTTPException(status_code=404, detail=f"未找到 patient_id='{payload.patient_id}' 的档案")

        # 删除患者所有照片与 OCR 文本目录。
        patient_dir = UPLOAD_ROOT / _safe_filename(payload.patient_id)
        if patient_dir.exists():
            shutil.rmtree(patient_dir, ignore_errors=True)

        collection.delete(ids=existing_ids)
        logger.success(f"档案删除成功: patient_id={payload.patient_id}, 删除 {len(existing_ids)} 条, operator={user.username}")
        audit.log("PATIENT_DELETE", payload.patient_id, user.username,
                  detail=f"删除患者全部档案，共 {len(existing_ids)} 条记录")
        return EHRDeleteResponse(
            code=200,
            message=f"患者 {payload.patient_id} 的全部档案、病历照片与 OCR 文本已删除",
            patient_id=payload.patient_id,
            deleted_count=len(existing_ids)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"档案删除失败: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")



# ── 操作审计日志查询 ─────────────────────────────────────────────
@router.get("/ehr/audit", summary="查询操作审计日志（需要 ehr.audit_read 权限）")
async def get_audit_log(
    patient_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
    _admin: User = Depends(require_permission(PERM_EHR_AUDIT_READ)),
):
    """
    返回档案操作审计记录（按时间倒序）。

    - patient_id：筛选指定患者的操作记录
    - action：筛选操作类型（PATIENT_CREATE / PATIENT_UPDATE / PATIENT_DELETE /
               RECORD_UPLOAD / RECORD_DELETE）
    - limit：最多返回条数（默认 100，最大 500）
    """
    limit = min(limit, 500)
    records = audit.query(patient_id=patient_id, action=action, limit=limit)
    return {"code": 200, "total": len(records), "records": records}
