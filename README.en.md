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

# 2. Local LLM: install Ollama, then grab HuatuoGPT-o1-7B
#    See "Local LLM setup" below — the model name `huatuo_o1_7b`
#    is an in-project alias, `ollama pull huatuo_o1_7b` will 404.
curl -fsSL https://ollama.com/install.sh | sh

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

The project is wired to **HuatuoGPT-o1-7B** (a Chinese medical LLM, ~8 GB). In the code
the model is referenced as `huatuo_o1_7b` — this is a **local alias**, not a name
published on Ollama's official Registry. `ollama pull huatuo_o1_7b` will 404.

Pick one of the three methods below. The goal is the same either way:
**`ollama list` must show `huatuo_o1_7b:latest`** when you're done.

### Upstream sources

| Source | Link |
|---|---|
| 🤗 HuggingFace (original weights) | [FreedomIntelligence/HuatuoGPT-o1-7B](https://huggingface.co/FreedomIntelligence/HuatuoGPT-o1-7B) |
| 🤗 HuggingFace (GGUF quants) | [bartowski/HuatuoGPT-o1-7B-GGUF](https://huggingface.co/bartowski/HuatuoGPT-o1-7B-GGUF) |
| 📦 Ollama community (pre-packaged) | [cliu/HuatuoGPT-o1-7B](https://ollama.com/cliu/HuatuoGPT-o1-7B) |
| 📄 GitHub (upstream + paper) | [FreedomIntelligence/HuatuoGPT-o1](https://github.com/FreedomIntelligence/HuatuoGPT-o1) |

### Method A — pull from Ollama community (easiest)

```bash
# Needs internet once, ~8 GB
ollama pull cliu/HuatuoGPT-o1-7B:latest

# Rename to the alias this project uses
ollama cp cliu/HuatuoGPT-o1-7B:latest huatuo_o1_7b

# Verify
ollama list | grep huatuo_o1_7b
```

### Method B — import a GGUF from HuggingFace (most control)

Best for air-gapped installs or when you need to pick a specific quantization
(Q4_K_M balanced ≈ 4.7 GB, Q8_0 high-quality ≈ 8.1 GB):

```bash
# 1. Download a single GGUF file (pick any quant from the file list on HF)
#    Listing: https://huggingface.co/bartowski/HuatuoGPT-o1-7B-GGUF/tree/main
wget https://huggingface.co/bartowski/HuatuoGPT-o1-7B-GGUF/resolve/main/HuatuoGPT-o1-7B-Q4_K_M.gguf

# 2. Modelfile — mind the FROM path
cat > Modelfile <<'EOF'
FROM ./HuatuoGPT-o1-7B-Q4_K_M.gguf
PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER num_ctx 8192
TEMPLATE """<|im_start|>system
{{ .System }}<|im_end|>
<|im_start|>user
{{ .Prompt }}<|im_end|>
<|im_start|>assistant
"""
EOF

# 3. Register under the project's expected name
ollama create huatuo_o1_7b -f Modelfile

# 4. Verify
ollama list | grep huatuo_o1_7b
```

### Method C — use a different model (low-memory fallback)

The project isn't hard-bound to HuatuoGPT. If your machine can't carry a 7B model,
swap in any Ollama-supported Chinese model and override the env var:

```bash
ollama pull qwen2.5:3b
echo 'OLLAMA_MODEL_NAME=qwen2.5:3b' >> .env
```

> Heads-up: non-medical models occasionally emit extra prose around the strict-JSON
> task-card response, which can fail parsing. The project has a retry fallback, but
> for clinical use stick with HuatuoGPT-o1-7B.

### Start the Ollama service

```bash
# Linux (systemd installer starts it automatically):
systemctl status ollama

# macOS / manual:
ollama serve                      # foreground
# or background:
nohup ollama serve > /tmp/ollama.log 2>&1 &
```

Ollama listens on `http://localhost:11434` by default; that URL is baked into
[`app/core/config.py`](./app/core/config.py) as `OLLAMA_API_URL`. If you host Ollama
on a different machine, either run this project alongside it or point
`OLLAMA_API_URL` at the remote host.

### End-to-end smoke test (do this before wiring up the frontend)

```bash
# 1. Talk to Ollama directly
ollama run huatuo_o1_7b "Introduce yourself in one sentence."

# 2. Hit the generate endpoint (what this project uses under the hood)
curl -s http://localhost:11434/api/generate \
  -d '{"model":"huatuo_o1_7b","prompt":"What should a caregiver do for elderly dizziness?","stream":false}' \
  | head -c 300

# 3. Start the backend, then hit the health check
curl -s http://localhost:8000/health
```

### Common gotchas

| Symptom | Cause | Fix |
|---|---|---|
| `pull model manifest: file does not exist` | Model name not in Registry | Use Method A with `cliu/HuatuoGPT-o1-7B`, or Method B via `ollama create` |
| `connection refused :11434` | Ollama service not running | `ollama serve`, or `systemctl start ollama` |
| Nursing API returns `503 local LLM unavailable` | Ollama not ready / name mismatch | Confirm `ollama list` shows `huatuo_o1_7b:latest`; restart the backend |
| First inference is slow (10 s+) | Cold start — loading weights | Expected; warm it up once via `ollama run huatuo_o1_7b ""` |
| OOM on 16 GB machines | Q8 is tight at 16 GB | Use Method B with Q4_K_M, or drop to Method C |

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
