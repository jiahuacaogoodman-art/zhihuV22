<h1 align="center">ZhiHu YinBan · 智护银伴</h1>

<p align="center">
  <b>A 100% offline AI nursing copilot for elderly-care homes.</b><br>
  Records never leave the building. Photos never touch the cloud.
  Works fine when the internet doesn't.
</p>

<p align="center">
  <a href="./README.md">简体中文</a> | <b>English</b>
</p>

<p align="center">
  <img alt="python"  src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="fastapi" src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white">
  <img alt="chroma"  src="https://img.shields.io/badge/ChromaDB-0.5-3C1F85">
  <img alt="ollama"  src="https://img.shields.io/badge/Ollama-huatuo__o1__7b-000000?logo=ollama&logoColor=white">
  <img alt="offline" src="https://img.shields.io/badge/Runtime-100%25%20Offline-10B981">
  <img alt="license" src="https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-blue">
</p>

<p align="center">
  <a href="#-why">Why</a> ·
  <a href="#-what-it-does">What it does</a> ·
  <a href="#-architecture">Architecture</a> ·
  <a href="#-quick-start">Quick start</a> ·
  <a href="#-api-overview">API</a> ·
  <a href="#-project-layout">Layout</a> ·
  <a href="#-production-deployment">Deploy</a> ·
  <a href="#-roadmap">Roadmap</a>
</p>

---

## 🌱 Why

Small, local nursing homes share the same three pains:

- Dozens of elderly residents per nurse, **no way to keep care quality uniform**.
- Medical records are fragmented across paper, USB sticks and WeChat groups — **you can't plug that into an LLM**.
- Management's #1 fear is **"data in the cloud = liability"**, which kills most SaaS AI pitches on the first slide.

**ZhiHu YinBan** is the pragmatic middle ground: bring a real "LLM + RAG" workflow into the building — but keep every patient file, every photo and every AI decision log **on local disk**. One commodity server + LAN is all it needs.

---

## ✨ What it does

| For | Capabilities |
|---|---|
| **Admin / nurse station** | Manage resident profiles, upload medical-record photos, local OCR, query AI nursing advice, review decision history |
| **Caregiver / tablet** | Pick a resident, describe symptoms in plain language, generate a **checkable task card**, log abnormalities, auto-produce an SBAR handoff |
| **Safety rails** | AI only outputs executable nursing steps; anything involving medication dosage falls back to "ask the charge nurse / doctor" — never a prescription |

### What sets it apart from a typical RAG demo

- **🧩 Hybrid retrieval with source-type weighting**
  Dense (`bge-small-zh`) + character **bi-gram BM25** + RRF fusion — tuned for Chinese drug / disease names that are usually 2–4 characters, no jieba required.
  Source weights: `profile 1.0 > uploaded record 0.95 > observation 0.90 > past decision 0.85`.
- **🧠 Closed-loop decision memory (L4)**
  Every AI suggestion is written back into the same vector store (`doc_type=decision_log`) → on the next query, **past decisions are evidence themselves**. With outcome feedback (effective / partial / ineffective), the model can see *"last time we did X for this same resident, this is how it turned out."*
- **📇 Citation-first answers**
  Replies **must** cite evidence as `[E1] [E2]`. If evidence is insufficient, the answer literally says *"insufficient evidence"* instead of hallucinating. The frontend turns each citation into a jump link to the original photo / profile segment.
- **🃏 Structured task cards, not blobs of prose**
  Ollama emits strict JSON; the backend validates against a whitelist; the frontend renders a checkable checklist + re-check schedule + do-not-do list + SBAR handoff — every item is actionable and auditable.
- **🔌 Fail honestly**
  Ollama down? The API returns a real `503`. OCR not installed? Metadata says `ocr_status=unavailable`. No fake results, ever.

---

## 🧱 Architecture

```
                   ┌────────────────────────────────────────────────┐
 Admin UI          │                                                │
 (index.html)      │              FastAPI + Uvicorn                 │
 Caregiver UI      │    /api/ehr/*    /api/nursing/*    /uploads    │
 (nurse.html)      └──────┬──────────────┬──────────────┬───────────┘
                          │              │              │
                          ▼              ▼              ▼
                 ┌────────────────┐ ┌─────────┐ ┌──────────────────┐
                 │ HybridRetriever│ │   OCR   │ │  Ollama (local)  │
                 │ Dense + BM25  │ │RapidOCR │ │  huatuo_o1_7b    │
                 │ + RRF fusion  │ │Tesseract│ │  JSON task card  │
                 └──────┬─────────┘ └────┬────┘ └─────────┬────────┘
                        │                │                │
                        ▼                ▼                ▼
                 ┌───────────────────────────────────────────────┐
                 │  ChromaDB (PersistentClient, local disk)      │
                 │  patient_profile / medical_record_upload /    │
                 │  observation / decision_log                   │
                 └───────────────────────────────────────────────┘

                   Embedding: BAAI/bge-small-zh-v1.5 (CPU-friendly)
```

| Layer | Choice | Purpose |
|---|---|---|
| Web framework | FastAPI 0.115 + Uvicorn 0.32 | REST + SSE + static hosting |
| Validation | Pydantic 2.10 | Request / response schemas |
| Vector store | ChromaDB 0.5 (PersistentClient) | Profiles / records / observations / decision logs |
| Embedding | sentence-transformers + `BAAI/bge-small-zh-v1.5` | Lightweight Chinese, CPU is fine |
| OCR | RapidOCR (ONNX) → Tesseract (`chi_sim`) fallback | Fully offline record-photo OCR |
| LLM | Ollama + `huatuo_o1_7b` | Nursing advice / task-card JSON |
| Image | Pillow | EXIF fix + contrast boost |
| Logging | loguru | Structured startup / request logs |

---

## 🚀 Quick start

### Requirements

| Item | Minimum | Recommended |
|---|---|---|
| OS | Linux / macOS / Windows | Ubuntu 22.04 LTS |
| Python | 3.10 | 3.11 |
| RAM | 16 GB | 32 GB |
| GPU | not required | optional, NVIDIA ≥ 8 GB VRAM for better latency |

### Three steps

```bash
# 1. Clone & install
git clone https://github.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Local LLM: install Ollama, then one-shot model setup
#    (script handles download + rename + smoke test idempotently)
curl -fsSL https://ollama.com/install.sh | sh
bash scripts/setup_model.sh

# 3. OCR (Ubuntu — pick one or both)
sudo apt install -y tesseract-ocr tesseract-ocr-chi-sim
pip install rapidocr_onnxruntime   # optional, better Chinese accuracy

# Run
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open:

- Admin UI: <http://localhost:8000/>
- Caregiver UI: <http://localhost:8000/nurse>
- Health check: <http://localhost:8000/health>

> The first launch downloads `bge-small-zh-v1.5` (~100 MB) into `~/.cache/torch/sentence_transformers/`.
> **After that, the whole machine can run fully offline.**

---

## 🤖 Local LLM setup (HuatuoGPT-o1-7B)

The project is wired to **[HuatuoGPT-o1-7B](https://huggingface.co/FreedomIntelligence/HuatuoGPT-o1-7B)**
— a Chinese medical LLM, ~8 GB. In the code it's referenced as `huatuo_o1_7b`, which is a
**local alias**, not a name published on Ollama's official Registry.
`ollama pull huatuo_o1_7b` will 404 directly.

### One-shot install (recommended)

Make sure [Ollama](https://ollama.com/) is installed and running, then:

```bash
bash scripts/setup_model.sh
```

The script: checks Ollama is up → pulls 8 GB of weights from the
[community package](https://ollama.com/cliu/HuatuoGPT-o1-7B) → aliases them to `huatuo_o1_7b` →
runs a smoke test. Expected output:

```
[ ok ] ollama installed
[ ok ] ollama service responding at :11434
[ ok ] cliu/HuatuoGPT-o1-7B:latest already present, skipping pull
[ ok ] alias huatuo_o1_7b already present, skipping
[ ok ] model replied: Hi! I'm HuatuoGPT...
=== All set ===
```

The script is **idempotent** — safe to re-run after a network interruption.

### Can't run a 7B model? Fall back to a smaller one (30 seconds)

If your box has less than 16 GB RAM the 7B model will OOM. Swap in Qwen 2.5 3B:

```bash
ollama pull qwen2.5:3b
echo 'OLLAMA_MODEL_NAME=qwen2.5:3b' >> .env
```

> Heads-up: non-medical models occasionally add extra prose around the strict-JSON
> task-card response, which can fail parsing. The project has a retry fallback, but
> for clinical use stick with HuatuoGPT-o1-7B.

### Verify the install

```bash
# Check the alias exists
ollama list | grep huatuo_o1_7b

# Talk to it (first cold start takes 5–30 s)
ollama run huatuo_o1_7b "What should a caregiver do for elderly dizziness?"
```

### Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| Script says "ollama not found" | Ollama not installed | `curl -fsSL https://ollama.com/install.sh \| sh` |
| Script says "service not responding at :11434" | Ollama daemon not running | Linux: `sudo systemctl start ollama`; macOS: open Ollama.app |
| `pull` stalls / times out | Network can't reach the Registry | Use a proxy, re-run `bash scripts/setup_model.sh` |
| Nursing API returns `503 local LLM unavailable` | Ollama not ready when backend started | `ollama list` shows `huatuo_o1_7b:latest`; restart the backend |
| First inference takes 10 s+ | Cold start loading weights | Expected; pre-warm with `ollama run huatuo_o1_7b ""` |
| OOM on 16 GB | 7B is tight at 16 GB | Use the smaller-model fallback above |

<details>
<summary><b>Advanced: air-gapped install / custom quantization (import from GGUF)</b></summary>

For offline machines or when you need a specific quantization level
(Q4_K_M 4.7 GB balanced / Q6_K 6.25 GB near-lossless / Q8_0 8.10 GB max),
skip the one-shot script and import a GGUF directly from HuggingFace:

```bash
# 1. Download GGUF (huggingface-cli is more reliable than wget, supports resumption)
pip install -U "huggingface_hub[cli]"
huggingface-cli download bartowski/HuatuoGPT-o1-7B-GGUF \
  --include "HuatuoGPT-o1-7B-Q4_K_M.gguf" \
  --local-dir ./

# 2. Modelfile — don't omit the stop tokens, or the model will keep generating
#    the next fake "user" turn after its answer
cat > Modelfile <<'EOF'
FROM ./HuatuoGPT-o1-7B-Q4_K_M.gguf

TEMPLATE """<|im_start|>system
{{ .System }}<|im_end|>
<|im_start|>user
{{ .Prompt }}<|im_end|>
<|im_start|>assistant
"""

PARAMETER stop "<|im_start|>"
PARAMETER stop "<|im_end|>"
PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER num_ctx 8192
PARAMETER num_predict 2048
EOF

# 3. Register under the project's expected alias
ollama create huatuo_o1_7b -f Modelfile

# 4. Verify
ollama list | grep huatuo_o1_7b
```

**Which quant to pick**:

| Hardware | Recommended | Size |
|---|---|---|
| 16 GB RAM, CPU only | `Q4_K_M` | 4.68 GB |
| 32 GB RAM or 8 GB VRAM | `Q6_K` | 6.25 GB |
| 16 GB+ VRAM GPU | `Q8_0` | 8.10 GB |
| Apple Silicon (M1/M2/M3) | `IQ4_NL` | 4.44 GB |

Full quantization list: [bartowski/HuatuoGPT-o1-7B-GGUF](https://huggingface.co/bartowski/HuatuoGPT-o1-7B-GGUF/tree/main).

</details>

---

## 🔌 API overview

All endpoints live under `/api/*`. No auth by default — designed for a LAN deployment. If you expose it to the internet, put a reverse proxy with Basic Auth / OAuth in front.

### EHR management

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/ehr/patients` | Create a resident profile |
| `GET`  | `/api/ehr/patients` | List all residents |
| `GET`  | `/api/ehr/patients/{patient_id}` | Get one resident |
| `PUT`  | `/api/ehr/patients/{patient_id}` | Update a profile |
| `DELETE` | `/api/ehr/patients/{patient_id}` | Delete a resident (plus photos / OCR text) |
| `POST` | `/api/ehr/records/upload` | Upload a record photo → OCR → index into vector store |
| `GET`  | `/api/ehr/records/{patient_id}` | List a resident's record photos + OCR text |
| `DELETE` | `/api/ehr/records/{doc_id}` | Delete one record |

### Nursing decision / task cards

| Method | Path | Description |
|---|---|---|
| `GET`  | `/api/nursing/patient/{patient_id}` | Caregiver-facing profile summary |
| `POST` | `/api/nursing/decision` | RAG inference (hybrid retrieval + citations + memory) |
| `POST` | `/api/nursing/decision/stream` | Same, SSE streaming |
| `POST` | `/api/nursing/optimize_prompt` | Rewrite plain-language symptoms into clinical phrasing |
| `GET`  | `/api/nursing/decisions?patient_id=...` | Query past decisions |
| `PATCH`| `/api/nursing/decisions/{decision_id}/outcome` | Feed back outcome (effective / partial / ineffective) |

> **Example** — one RAG decision call:
>
> ```bash
> curl -X POST http://localhost:8000/api/nursing/decision \
>   -H 'Content-Type: application/json' \
>   -d '{"patient_id": "p002", "symptom": "BP 180/110 this afternoon, headache"}'
> ```

---

## 📁 Project layout

```
.
├── app/
│   ├── core/config.py          # Models, paths, hyperparams, prompt templates
│   ├── routers/
│   │   ├── ehr.py              # Profile CRUD + record upload + OCR
│   │   └── nursing.py          # RAG decision + task card + event loop
│   └── services/
│       ├── retrieval.py        # Hybrid retrieval (Dense + BM25 + RRF)
│       ├── decision_memory.py  # Decision memory + outcome feedback
│       ├── llm_service.py      # Ollama client (stream / non-stream)
│       └── ocr_service.py      # RapidOCR → Tesseract fallback
├── static/
│   ├── index.html              # Admin UI
│   ├── nurse.html              # Caregiver UI
│   ├── design/                 # Liquid-glass design system
│   ├── pet/                    # Desktop-pet animations
│   └── sw.js / manifest.json   # PWA support
├── scripts/run.sh              # One-shot launcher
├── main.py                     # FastAPI entrypoint
└── requirements.txt
```

### On-disk data directories

```
./local_ehr_db/                           # ChromaDB (most important, back this up!)
./local_ehr_uploads/<pid>/photos/         # Original record photos
./local_ehr_uploads/<pid>/ocr/            # OCR text output
./local_nursing_events/events.json        # Nursing event stream
~/.cache/torch/sentence_transformers/     # Embedding offline cache
~/.ollama/models/                         # huatuo_o1_7b weights
```

---

## 🏭 Production deployment

### Run it under systemd (recommended)

`/etc/systemd/system/zhihuyinban.service`:

```ini
[Unit]
Description=ZhiHu YinBan Backend
After=network.target

[Service]
User=zhihu
WorkingDirectory=/opt/zhihuyinban
ExecStart=/opt/zhihuyinban/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zhihuyinban
journalctl -u zhihuyinban -f   # live logs
```

### Fully offline install

1. On an **online machine**, run `SentenceTransformer("BAAI/bge-small-zh-v1.5")` once, then tar up the whole `~/.cache/torch/sentence_transformers/` directory.
2. `pip download -r requirements.txt -d wheels/` to bundle every wheel.
3. On the target machine: `pip install --no-index --find-links=./wheels -r requirements.txt`.
4. Drop the model cache at the same `~/.cache/torch/sentence_transformers/` path and you're done.

### Backups

**`local_ehr_db/` holds everything. Back it up offsite, daily.**
`restic` / `borg` work great; the 5-second version is `tar czf backup-$(date +%F).tgz local_ehr_db/`.

---

## 🗺️ Roadmap

- [x] Profile CRUD + record-photo OCR
- [x] Hybrid retrieval (Dense + BM25 + RRF)
- [x] Task cards (strict JSON)
- [x] SSE streaming
- [x] Decision memory + outcome feedback
- [ ] Multi-tenant data isolation (`tenant_id`)
- [ ] PDF export for SBAR handoffs
- [ ] Offline PWA bundle for caregiver tablets
- [ ] Fine-tuning script: feed local decision logs back into a huatuo LoRA

---

## ⚠️ Disclaimer

AI-generated advice is **a nursing aid only — not a diagnosis, not a prescription.**
For anything dosage-related the system intentionally defers to the charge nurse or physician.
In emergencies, call a doctor or start your facility's emergency protocol.

---

## 📜 License

This project is licensed under the **[PolyForm Noncommercial License 1.0.0](./LICENSE)** — **noncommercial use only**.

- ✅ Allowed: personal study / research, teaching, public-interest use, internal use inside nonprofit hospitals and elderly-care homes, and modification / redistribution for any noncommercial purpose (this license must be preserved).
- ❌ Not allowed: any commercial use of the project or its derivatives — including selling it as a product or SaaS, offering paid deployment / hosting, or bundling it into commercial software.
- 📮 For a **commercial license**, please reach out separately: [@jiahuacaogoodman-art](https://github.com/jiahuacaogoodman-art)

Copyright © 2026 [jiahuaCao](https://github.com/jiahuacaogoodman-art)

---

<p align="center">
  If this helped you, a ⭐ goes a long way — it's what keeps me writing.
</p>
