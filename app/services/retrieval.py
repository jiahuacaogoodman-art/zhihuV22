# -*- coding: utf-8 -*-
"""
@File    : services/retrieval.py
@Desc    : 混合检索服务
           - 稠密向量：复用 ChromaDB + bge-small-zh 的 embeddings
           - 稀疏关键词：基于字符 bi-gram 的 BM25（纯 Python，无新依赖）
           - 融合：RRF (Reciprocal Rank Fusion)
           - 输出：结构化 Evidence 列表（带 evidence_id、source_type、snippet）
                 + 可直接拼进 Prompt 的引用块（[E1]..[En]）

           这是决策记忆（L4）的基础——决策日志也会作为 doc_type=decision_log
           的文档写入同一个 collection，因此同一个检索入口就能自然"回忆"过去。
"""
from __future__ import annotations

import re
import math
from collections import Counter
from dataclasses import dataclass, asdict, field
from typing import Any, Iterable


# ── 字符 bi-gram 分词 ──────────────────────────────────────────
# 中文医疗场景里药名/病名/检验指标大多是 2–4 字短词，字符 bi-gram 足够稳，
# 也不用带 jieba 这种外部词典依赖；英文 / 数字按空白切。
_EN_TOKEN = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9\.\-/]*")


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    text = text.lower()
    tokens: list[str] = []
    # 抽英文/数字 token
    for m in _EN_TOKEN.finditer(text):
        tokens.append(m.group(0))
    # 中文字符 bi-gram + 单字（保证"糖尿病"等完全命中也能打分）
    chinese = re.sub(r"[^\u4e00-\u9fff]+", " ", text)
    for chunk in chinese.split():
        tokens.extend(chunk)
        for i in range(len(chunk) - 1):
            tokens.append(chunk[i:i + 2])
    return tokens


# ── BM25 ─────────────────────────────────────────────────────
class BM25:
    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus_tokens
        self.N = len(corpus_tokens)
        self.doc_len = [len(doc) for doc in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / self.N) if self.N else 0.0
        self.tf: list[Counter] = [Counter(doc) for doc in corpus_tokens]
        # document frequency
        df: Counter = Counter()
        for doc in corpus_tokens:
            for term in set(doc):
                df[term] += 1
        # idf（BM25 plus-style，避免负值）
        self.idf: dict[str, float] = {
            term: math.log(1 + (self.N - f + 0.5) / (f + 0.5))
            for term, f in df.items()
        }

    def score(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * self.N
        if self.N == 0 or self.avgdl == 0:
            return scores
        for term in query_tokens:
            idf = self.idf.get(term)
            if not idf:
                continue
            for i, tf in enumerate(self.tf):
                f = tf.get(term, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                scores[i] += idf * (f * (self.k1 + 1)) / denom
        return scores


# ── Evidence 结构 ────────────────────────────────────────────
@dataclass
class Evidence:
    evidence_id: str           # E1, E2, ...（在一次检索内）
    doc_id: str                # ChromaDB 里的原始 id
    source_type: str           # patient_profile / medical_record_upload / decision_log / observation / unknown
    source_label: str          # 人类可读："基本档案" / "病历OCR" / "过往决策(3天前)" / "观察记录"
    snippet: str               # 裁剪后的片段（约 <=180 字）
    score: float               # 融合分
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ── 源类型权重（L2 里提到的"用户修正 > 原始 OCR"之类） ─────
SOURCE_WEIGHTS: dict[str, float] = {
    "patient_profile": 1.00,
    "medical_record_upload": 0.95,
    "decision_log": 0.85,          # 过去决策稍微降权（新证据优先）
    "observation": 0.90,
    "unknown": 0.80,
}


SOURCE_LABELS: dict[str, str] = {
    "patient_profile": "基本档案",
    "medical_record_upload": "病历OCR",
    "decision_log": "过往决策",
    "observation": "观察记录",
    "unknown": "历史档案",
}


def _infer_source_type(meta: dict | None) -> str:
    if not meta:
        return "unknown"
    st = meta.get("source_type")
    if st:
        return st
    dt = meta.get("doc_type")
    if dt in {"patient_profile", "medical_record_upload", "decision_log", "observation"}:
        return dt
    # 兼容老数据：没 doc_type 的一律视为基本档案（与 ehr 路由里 _is_profile 一致）
    return "patient_profile"


def _clip_snippet(text: str, max_len: int = 220) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _best_snippet(doc: str, query: str, max_len: int = 220) -> str:
    """找到 query 中关键词在 doc 里出现的位置，截出一段上下文；找不到就截开头。"""
    if not doc:
        return ""
    doc_norm = doc
    tokens = [t for t in tokenize(query) if len(t) >= 2]
    # 找第一个命中
    hit = -1
    for t in tokens:
        idx = doc_norm.find(t)
        if idx >= 0:
            hit = idx
            break
    if hit < 0:
        return _clip_snippet(doc, max_len)
    start = max(0, hit - 40)
    end = min(len(doc_norm), start + max_len)
    snippet = doc_norm[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(doc_norm):
        snippet = snippet + "…"
    return re.sub(r"\s+", " ", snippet).strip()


# ── 主入口 ────────────────────────────────────────────────────

# 进程级 BM25 索引缓存。
# Key:   (patient_id, frozenset(doc_ids))   ← doc_ids 变化时自动失效
# Value: (BM25 instance, sub_ids, sub_docs, sub_metas)
#
# 设计考量：
#   - 养老院场景下单个患者的档案量通常 < 200 条，BM25 对象仅几 KB
#   - 读档案时 Chroma.get() 已经是主要延迟来源；命中缓存后 BM25 步骤
#     从 O(N·|tokens|) 降到 O(1)
#   - 缓存失效条件：frozenset(doc_ids) 变化（增删档案/病历）
#   - 最大缓存条目数：MAX_BM25_ENTRIES（默认 128 患者），超出时删最旧
import threading as _threading

MAX_BM25_ENTRIES = 128

_bm25_cache: dict = {}         # key → (BM25, sub_ids, sub_docs, sub_metas, frozenset)
_bm25_order: list = []         # 保持插入顺序，用于 LRU 淘汰
_bm25_lock = _threading.Lock()


def _bm25_put(cache_key: tuple, bm25_obj: "BM25", sub_ids, sub_docs, sub_metas) -> None:
    with _bm25_lock:
        if cache_key in _bm25_cache:
            _bm25_order.remove(cache_key)
        elif len(_bm25_order) >= MAX_BM25_ENTRIES:
            oldest = _bm25_order.pop(0)
            _bm25_cache.pop(oldest, None)
        _bm25_cache[cache_key] = (bm25_obj, sub_ids, sub_docs, sub_metas)
        _bm25_order.append(cache_key)


def _bm25_get(cache_key: tuple):
    with _bm25_lock:
        entry = _bm25_cache.get(cache_key)
        if entry:
            # 命中 → 移到末尾（LRU）
            _bm25_order.remove(cache_key)
            _bm25_order.append(cache_key)
        return entry


class HybridRetriever:
    def __init__(self, collection, embedding_function):
        self.collection = collection
        self.embedding_function = embedding_function

    def retrieve(
        self,
        patient_id: str,
        query: str,
        top_k: int = 5,
        dense_k: int = 12,
        bm25_k: int = 12,
        rrf_k: int = 60,
        include_source_types: Iterable[str] | None = None,
        exclude_source_types: Iterable[str] | None = None,
    ) -> list[Evidence]:
        """按 patient_id 过滤 + 混合检索，返回带 evidence_id 的证据列表。"""
        include = set(include_source_types) if include_source_types else None
        exclude = set(exclude_source_types) if exclude_source_types else set()

        # 1. 拉取该患者的全部文档（养老院场景单患者档案量不大，几百条内，
        #    BM25 直接在 Python 里跑够用；若未来量大再切到 Chroma 服务侧。）
        all_docs = self.collection.get(
            where={"patient_id": {"$eq": patient_id}},
            include=["documents", "metadatas"],
        )
        ids: list[str] = all_docs.get("ids", []) or []
        docs: list[str] = all_docs.get("documents", []) or []
        metas: list[dict] = all_docs.get("metadatas", []) or []
        if not ids:
            return []

        # 2. 按 source_type 过滤
        keep_idx: list[int] = []
        for i, meta in enumerate(metas):
            st = _infer_source_type(meta)
            if include and st not in include:
                continue
            if st in exclude:
                continue
            keep_idx.append(i)
        if not keep_idx:
            return []
        sub_ids = [ids[i] for i in keep_idx]
        sub_docs = [docs[i] or "" for i in keep_idx]
        sub_metas = [metas[i] or {} for i in keep_idx]

        # 3. BM25 打分（稀疏）— 命中缓存时跳过 tokenize + BM25.__init__
        cache_key = (patient_id, frozenset(sub_ids))
        cached = _bm25_get(cache_key)
        if cached is not None:
            bm25, sub_ids, sub_docs, sub_metas = cached
        else:
            corpus_tokens = [tokenize(d) for d in sub_docs]
            bm25 = BM25(corpus_tokens)
            _bm25_put(cache_key, bm25, sub_ids, sub_docs, sub_metas)
        bm25_scores = bm25.score(tokenize(query))
        bm25_rank = sorted(range(len(sub_ids)), key=lambda i: bm25_scores[i], reverse=True)
        bm25_top = bm25_rank[:bm25_k]

        # 4. 稠密向量检索（只在该患者子集内）
        dense_top: list[int] = []
        try:
            qv = self.embedding_function.encode(query).tolist()
            # 用 Chroma 自带的 where 过滤 + 向量查询，比在 python 里手算余弦靠谱
            dense_res = self.collection.query(
                query_embeddings=[qv],
                n_results=min(dense_k, len(sub_ids)),
                where={"patient_id": {"$eq": patient_id}},
                include=["metadatas"],
            )
            dense_ids = (dense_res.get("ids") or [[]])[0]
            id_to_sub = {sid: si for si, sid in enumerate(sub_ids)}
            for did in dense_ids:
                si = id_to_sub.get(did)
                if si is not None:
                    dense_top.append(si)
        except Exception:
            # 向量检索失败时退化为纯 BM25
            dense_top = []

        # 5. RRF 融合
        rrf: dict[int, float] = {}
        for rank, si in enumerate(bm25_top):
            rrf[si] = rrf.get(si, 0.0) + 1.0 / (rrf_k + rank + 1)
        for rank, si in enumerate(dense_top):
            rrf[si] = rrf.get(si, 0.0) + 1.0 / (rrf_k + rank + 1)

        # 6. 源类型加权
        weighted: list[tuple[int, float]] = []
        for si, score in rrf.items():
            st = _infer_source_type(sub_metas[si])
            weighted.append((si, score * SOURCE_WEIGHTS.get(st, 1.0)))
        weighted.sort(key=lambda x: x[1], reverse=True)

        # 7. 组装 Evidence
        out: list[Evidence] = []
        for rank, (si, score) in enumerate(weighted[:top_k], start=1):
            meta = sub_metas[si]
            st = _infer_source_type(meta)
            label = SOURCE_LABELS.get(st, "历史档案")
            # 对过往决策，给个时间差提示，让 Prompt 里一眼能看懂"N 天前"
            ts = meta.get("timestamp") or meta.get("created_at") or meta.get("uploaded_at")
            if st == "decision_log" and ts:
                label = f"过往决策·{ts}"
            elif st == "medical_record_upload" and ts:
                label = f"病历OCR·{ts}"
            elif st == "observation" and ts:
                label = f"观察记录·{ts}"
            snippet = _best_snippet(sub_docs[si], query)
            out.append(Evidence(
                evidence_id=f"E{rank}",
                doc_id=sub_ids[si],
                source_type=st,
                source_label=label,
                snippet=snippet,
                score=round(float(score), 4),
                metadata={
                    "record_type": meta.get("record_type"),
                    "doc_type": meta.get("doc_type"),
                    "timestamp": ts,
                    "source_ref": meta.get("source_ref"),
                    "file_url": meta.get("file_url"),
                },
            ))
        return out


# ── 把证据列表变成 Prompt 引用块 ─────────────────────────────
def format_evidence_block(evidence: list[Evidence]) -> str:
    if not evidence:
        return "（未检索到相关档案）"
    lines = []
    for e in evidence:
        lines.append(f"[{e.evidence_id}] 来源：{e.source_label}\n    {e.snippet}")
    return "\n".join(lines)


def legacy_context_string(evidence: list[Evidence]) -> str:
    """保留旧的一整坨字符串给兼容路径。"""
    if not evidence:
        return "（未检索到相关档案）"
    return "\n---\n".join(e.snippet for e in evidence)
