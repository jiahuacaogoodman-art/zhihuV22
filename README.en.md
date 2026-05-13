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

### One-click deploy (recommended — 3 lines)

Just need Docker installed. Everything else is automatic — secrets, model download, GPU detection, service startup:

```bash
git clone https://github.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban
chmod +x scripts/setup.sh && ./scripts/setup.sh
```

Follow the wizard prompts (press Enter for defaults). In ~10 minutes you'll see `🎉 Deploy successful!` with your admin token.

| Page | URL |
|---|---|
| Admin UI | http://localhost:8000/ |
| Caregiver UI | http://localhost:8000/nurse |
| Health check | http://localhost:8000/health |

---

### Manual install (developers / no Docker)

<details>
<summary>Expand manual install steps</summary>

#### Requirements

| Item | Minimum | Recommended |
|---|---|---|
| OS | Linux / macOS / Windows | Ubuntu 22.04 LTS |
| Python | 3.10 | 3.12 |
| RAM | 16 GB | 32 GB |
| GPU | not required | optional, NVIDIA ≥ 8 GB VRAM for better latency |

#### Steps

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

# 4. Configure secrets (auth + PII encryption)
cp .env.example .env
# Then edit .env and set at least:
#   AUTH_TOKEN=$(openssl rand -hex 32)
#   PII_ENCRYPTION_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')

# Run
uvicorn main:app --host 0.0.0.0 --port 8000
```

> The first launch downloads `bge-small-zh-v1.5` (~100 MB) into `~/.cache/torch/sentence_transformers/`.
> **After that, the whole machine can run fully offline.**

</details>

---

## 🤖 Local LLM setup (HuatuoGPT-o1-7B)

The project is wired to **HuatuoGPT-o1-7B** (a Chinese medical LLM, ~5 GB
quantized). The Docker Compose stack handles model setup automatically — for
bare-metal installs, pick one of the methods below.

### Upstream sources

| Source | Link |
|---|---|
| 🤗 HuggingFace (original weights) | [FreedomIntelligence/HuatuoGPT-o1-7B](https://huggingface.co/FreedomIntelligence/HuatuoGPT-o1-7B) |
| 🤗 HuggingFace (GGUF quants — used by Compose) | [mradermacher/HuatuoGPT-o1-7B-GGUF](https://huggingface.co/mradermacher/HuatuoGPT-o1-7B-GGUF) |
| 🤗 HuggingFace (alt GGUF) | [bartowski/HuatuoGPT-o1-7B-GGUF](https://huggingface.co/bartowski/HuatuoGPT-o1-7B-GGUF) |
| 📦 Ollama community (pre-packaged) | [cliu/HuatuoGPT-o1-7B](https://ollama.com/cliu/HuatuoGPT-o1-7B) |
| 📄 GitHub (upstream + paper) | [FreedomIntelligence/HuatuoGPT-o1](https://github.com/FreedomIntelligence/HuatuoGPT-o1) |

### Method 0 — Docker Compose (zero-touch, recommended)

The `docker-compose.yml` ships with a `model-puller` service that pulls
`hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M` directly from HuggingFace on
first start. Nothing else to do — just `docker compose up -d`. See
[Production deployment → Option A](#option-a--docker-compose-recommended-).

### Method A — pull from HuggingFace via Ollama (bare metal)

Ollama natively understands `hf.co/...` URIs, so you can grab any community
GGUF directly. No Modelfile required.

```bash
# Pick a quant tag — Q4_K_M is the default, Q5_K_M for better quality
ollama pull hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M

# Tell the app to use it
echo 'OLLAMA_MODEL_NAME=hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M' >> .env

ollama list   # verify the tag appears
```

### Method B — pull from Ollama community

```bash
ollama pull cliu/HuatuoGPT-o1-7B:latest
echo 'OLLAMA_MODEL_NAME=cliu/HuatuoGPT-o1-7B:latest' >> .env
```

### Method C — local GGUF + Modelfile (air-gapped / custom prompts)

```bash
# 1. Drop a GGUF file next to the Modelfile
wget https://huggingface.co/bartowski/HuatuoGPT-o1-7B-GGUF/resolve/main/HuatuoGPT-o1-7B-Q4_K_M.gguf

# 2. Modelfile
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

# 3. Register under any name you like
ollama create my_huatuo -f Modelfile
echo 'OLLAMA_MODEL_NAME=my_huatuo' >> .env
```

### Method D — different model (low-memory fallback)

The project isn't hard-bound to HuatuoGPT. Swap in any Ollama-supported model:

```bash
ollama pull qwen2.5:3b
echo 'OLLAMA_MODEL_NAME=qwen2.5:3b' >> .env
```

> Heads-up: non-medical models occasionally emit extra prose around the
> strict-JSON task-card response, which can fail parsing. The project has a
> retry fallback, but for clinical use stick with HuatuoGPT-o1-7B.

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

## 🔐 Authentication & PII encryption

The system runs in **one of three auth modes**, chosen automatically at startup:

| Mode | When | What happens |
|---|---|---|
| **Disabled** | `AUTH_TOKEN` empty *and* user store empty | All endpoints open. Dev / LAN only. |
| **Bootstrap (single token)** | `AUTH_TOKEN` set, user store empty | First request bootstraps an `admin` user whose API key equals `AUTH_TOKEN`. |
| **Multi-user** | User store has any user | Every request must carry a valid `X-Auth-Token` issued via `/api/auth/tokens`. |

Three roles are enforced server-side: `admin` (full access incl. audit log), `nurse` (read EHR + write nursing events), `caregiver` (own tasks only).

```bash
# Bootstrap an admin token on first deploy
export AUTH_TOKEN=$(openssl rand -hex 32)

# Create a nurse account
curl -X POST http://localhost:8000/api/auth/users \
  -H "X-Auth-Token: $AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"wang_nurse","display_name":"Nurse Wang","role":"nurse"}'

# Issue an API key (the token in the response is shown ONCE — save it now)
curl -X POST http://localhost:8000/api/auth/tokens \
  -H "X-Auth-Token: $AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"usr_xxxxx","label":"mobile-app"}'

# Inspect the current identity / mode
curl http://localhost:8000/api/auth/me -H "X-Auth-Token: $TOKEN"
```

### PII field-level encryption

Set `PII_ENCRYPTION_KEY` (Fernet, 44 chars, URL-safe base64) and 10 sensitive
fields (name, gov ID, phone, address, contacts, allergies, …) are stored
encrypted at rest. Audit-log diffs strip the same fields automatically so logs
don't leak the very PII they're meant to track.

Generate a key with:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Both `AUTH_TOKEN` and `PII_ENCRYPTION_KEY` belong in `.env` (which is git-ignored).
Rotating `PII_ENCRYPTION_KEY` requires re-encrypting existing rows — automated
key rotation is on the [roadmap](#%EF%B8%8F-roadmap).

---

## 🔌 API overview

All endpoints live under `/api/*` and are protected by **token-based authentication** (see [Authentication & PII encryption](#-authentication--pii-encryption) below). For LAN-only deployments you can disable auth by leaving `AUTH_TOKEN` empty *and* keeping the user store empty — but doing this on a public network is not recommended. Always put a reverse proxy with TLS in front when exposing the service to the internet.

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
│   ├── core/config.py              # Models, paths, hyperparams, prompt templates
│   ├── middleware/auth.py          # 3-mode token auth (disabled / bootstrap / multi-user)
│   ├── models/
│   │   ├── schemas.py              # EHR + nursing pydantic models
│   │   └── auth_schemas.py         # User / API key / role models
│   ├── routers/
│   │   ├── auth.py                 # /api/auth/* (users, tokens, me)
│   │   ├── ehr.py                  # Profile CRUD + record upload + OCR
│   │   └── nursing.py              # RAG decision + task card + event loop
│   └── services/
│       ├── retrieval.py            # Hybrid retrieval (Dense + BM25 + RRF)
│       ├── decision_memory.py      # Decision memory + outcome feedback
│       ├── llm_service.py          # Ollama client (stream / non-stream)
│       ├── ocr_service.py          # RapidOCR → Tesseract fallback
│       ├── user_store.py           # User + API-key storage (SQLite)
│       ├── audit_log.py            # Operation audit + PII-redacted diff
│       ├── pii_crypto.py           # Fernet encryption for 10 sensitive fields
│       ├── event_store.py          # Nursing event SQLite persistence
│       ├── permissions.py          # RBAC helpers (admin / nurse / caregiver)
│       └── protocol_loader.py      # Hot-reloadable protocol templates
├── data/protocols.yaml             # Editable nursing protocol templates
├── static/
│   ├── index.html                  # Admin UI
│   ├── nurse.html                  # Caregiver UI
│   ├── design/                     # Liquid-glass design system
│   ├── pet/                        # Desktop-pet animations
│   └── sw.js / manifest.json       # PWA support
├── scripts/run.sh                  # One-shot launcher
├── main.py                         # FastAPI entrypoint
├── requirements.txt
├── Dockerfile
├── docker-compose.yml              # One-shot stack: app + ollama + model auto-pull
├── docker-compose.gpu.yml          # Optional NVIDIA GPU overlay
└── .env.example                    # Environment template
```

### On-disk data directories

```
./local_ehr_db/                           # ChromaDB (most important, back this up!)
./local_ehr_uploads/<pid>/photos/         # Original record photos
./local_ehr_uploads/<pid>/ocr/            # OCR text output
./local_auth/users.db                     # Users + hashed API keys
./local_audit_log/audit.db                # Operation audit trail (compliance)
./local_nursing_events/events.db          # Nursing event stream
~/.cache/torch/sentence_transformers/     # Embedding offline cache
~/.ollama/models/                         # LLM weights
```

---

## 🏭 Production deployment

### Option 0 · One-click setup wizard (most recommended ⭐⭐⭐)

Zero manual configuration — the script auto-detects everything, generates secrets, picks a model, and launches Docker:

```bash
git clone https://github.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban
chmod +x scripts/setup.sh
./scripts/setup.sh
```

The wizard will:
1. Detect Docker / Docker Compose / NVIDIA GPU
2. Auto-generate AUTH_TOKEN + PII_ENCRYPTION_KEY (or let you paste existing ones)
3. Ask for LLM backend: local Ollama or remote GPU API
4. Let you pick model quantization (Q3/Q4/Q5/Q8/custom)
5. Write `.env`
6. Run `docker compose up -d`
7. Wait for model download + app health check
8. **Print access URL + admin token on success**

Just press Enter through the prompts. Takes ~10 minutes on first run (model download depends on network speed).

---

### Option A · Docker Compose manual setup (if you prefer not to run the script)

Same thing as setup.sh, but you do each step yourself:

```bash
# 1. Prepare env file
cp .env.example .env

# 2. Fill in the two required secrets
echo "AUTH_TOKEN=$(openssl rand -hex 32)" >> .env
echo "PII_ENCRYPTION_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" >> .env

# 3. Start the stack
#    First run pulls ~4.8 GB from HuggingFace (HuatuoGPT-o1-7B Q4_K_M).
docker compose up -d

# 4. Watch the model download finish
docker compose logs -f model-puller   # wait for "[model-puller] done."
docker compose logs -f app            # backend logs

# 5. Open http://localhost:8000
```

**What's inside?**

| Service | Role | Notes |
|---|---|---|
| `ollama` | Local LLM inference engine | Bound to `127.0.0.1:11434` only — never exposed publicly |
| `model-puller` | One-shot container that runs `ollama pull` against HuggingFace | Exits instantly if the model is already cached |
| `app` | ZhiHu YinBan backend + UI | Port `8000` |

**Default model** = `hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M` (~4.8 GB, runs on CPU).
Override via `.env`:

```env
# Tighter memory budget (~3.9 GB)
OLLAMA_MODEL_NAME=hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q3_K_M
# Recommended quality (~5.5 GB, needs ~12 GB RAM)
OLLAMA_MODEL_NAME=hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q5_K_M
# Near-lossless (~8.2 GB, needs 16 GB+ RAM)
OLLAMA_MODEL_NAME=hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q8_0
# Or swap to a different model entirely
OLLAMA_MODEL_NAME=qwen2.5:7b
```

**NVIDIA GPU?** Layer the GPU overlay on top:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
docker exec yinban-ollama nvidia-smi
```

> Requires the [NVIDIA Container Toolkit](https://github.com/NVIDIA/nvidia-container-toolkit) on the host.

**Day-2 ops:**

```bash
docker compose ps                 # status
docker compose logs -f app        # tail backend logs
docker compose exec app sh        # shell into the app container
docker compose restart app        # restart only the backend
docker compose down               # stop services, KEEP volumes
docker compose down -v            # WIPE everything (model, EHR, audit, ...)
```

**Volumes (preserved across `docker compose down`):**

| Volume | Contents | Back up? |
|---|---|---|
| `ollama_models` | LLM weights | No (can re-download) |
| `ehr_db` | ChromaDB vectors (records) | ✅ Yes |
| `ehr_uploads` | Original record photos + OCR | ✅ Yes |
| `auth_data` | Users + API keys | ✅ Yes |
| `audit_log` | Operation audit trail | ✅ Yes (compliance) |
| `nursing_events` | Nursing event stream | ✅ Yes |

Backup snippet:
```bash
docker run --rm \
  -v zhihu-yinban_ehr_db:/src/ehr_db:ro \
  -v zhihu-yinban_auth_data:/src/auth_data:ro \
  -v zhihu-yinban_audit_log:/src/audit_log:ro \
  -v zhihu-yinban_ehr_uploads:/src/ehr_uploads:ro \
  -v zhihu-yinban_nursing_events:/src/nursing_events:ro \
  -v $(pwd):/dst alpine \
  tar czf /dst/yinban-backup-$(date +%F).tgz -C /src .
```

---

### Option B · Single container (debug only)

Use this only when you already run Ollama yourself somewhere else:

```bash
docker build -t zhihu-yinban .
docker run -d --name yinban \
  -p 8000:8000 \
  -e AUTH_TOKEN=$(openssl rand -hex 32) \
  -e PII_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  -e OLLAMA_API_URL=http://host.docker.internal:11434/api/generate \
  -e OLLAMA_MODEL_NAME=hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M \
  --add-host=host.docker.internal:host-gateway \
  -v yinban_ehr_db:/app/local_ehr_db \
  -v yinban_ehr_uploads:/app/local_ehr_uploads \
  -v yinban_auth:/app/local_auth \
  -v yinban_audit_log:/app/local_audit_log \
  -v yinban_nursing_events:/app/local_nursing_events \
  zhihu-yinban
```

> ⚠️ All five volumes are required. Miss any one and you'll lose data on container recreate. Compose handles this for you — prefer Option A.

---

### Option C · systemd on bare metal

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
- [x] Decision memory + outcome feedback (L4 closed loop)
- [x] User identity + multi-API-key + roles (admin / nurse / caregiver)
- [x] Operation audit log (every write captured)
- [x] PII field-level encryption (10 fields, Fernet)
- [x] Audit-diff PII redaction
- [x] Nursing events persisted to SQLite
- [x] Hot-reloadable nursing protocol templates
- [x] One-shot Docker Compose with auto HuggingFace model pull
- [ ] Multi-tenant data isolation (`tenant_id`)
- [ ] PDF export for SBAR handoffs
- [ ] Offline PWA bundle for caregiver tablets
- [ ] Automated key rotation
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
- 🏥 **Private elderly-care institutions** interested in commercial cooperation can contact for the production version: **jiahuacaogoodman@gmail.com**

Copyright © 2026 [jiahuaCao](https://github.com/jiahuacaogoodman-art)

---

<p align="center">
  If this helped you, a ⭐ goes a long way — it's what keeps me writing.
</p>
