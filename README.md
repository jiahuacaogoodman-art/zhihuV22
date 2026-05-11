<h1 align="center">智护银伴 · ZhiHu YinBan</h1>

<p align="center">
  <b>一个 100% 本地运行的养老院 AI 护理辅助系统</b><br>
  档案不出院、照片不上云、断网也能给出可打卡的护理任务卡。
</p>

<p align="center">
  <b>简体中文</b> | <a href="./README.en.md">English</a>
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
  <a href="#-为什么做这个">为什么做这个</a> ·
  <a href="#-核心能力">核心能力</a> ·
  <a href="#-系统架构">系统架构</a> ·
  <a href="#-快速开始">快速开始</a> ·
  <a href="#-api-一览">API</a> ·
  <a href="#-目录结构">目录结构</a> ·
  <a href="#-生产部署">部署</a> ·
  <a href="#-路线图">路线图</a>
</p>

---

## 🌱 为什么做这个

基层养老院面对的真实矛盾是:

- 老人多、护工人均看护数高,**专业经验很难均质化**;
- 病历碎在纸上、U 盘里、微信群里,**AI 想用却无从入手**;
- 院方最担心"**数据上云 = 合规和责任**",所以很多云端 AI 方案直接被一票否决。

**智护银伴**的目标是让养老院能把"大模型 + RAG"这套东西真正用起来 —— 前提是:所有档案、病历照片、AI 决策记录,**全部保存在本机磁盘**,整套系统在一台普通服务器 + 局域网里就能跑。

---

## ✨ 核心能力

| 面向 | 能做的事 |
|---|---|
| **管理端 / 护士站** | 录入与编辑老人档案、上传病历照片、本地 OCR 文字识别、查询 AI 护理建议、查看历史决策 |
| **护工端 / 平板手机** | 选择老人、口语化描述症状、一键生成**可打卡护理任务卡**、记录异常观察、归档并自动生成 SBAR 交接单 |
| **风控边界** | AI 只产出"可执行的护理步骤",涉及给药一律提示"交责任护士/医生核对",不构成处方 |

### 区别于普通 RAG Demo 的几件事

- **🧩 混合检索 + 源类型加权**
  稠密 (bge-small-zh) + 字符 bi-gram BM25 + RRF 融合,针对中文药名/病名大量 2~4 字短词的场景做了特化,无需 jieba。
  源类型打分:`档案 1.0 > 病历上传 0.95 > 观察记录 0.90 > 历史决策 0.85`。
- **🧠 决策记忆闭环 (L4)**
  每次 AI 建议 → 写回同一个向量库 (`doc_type=decision_log`) → 下次检索时**过去的决策就是证据**,再配合 outcome 回填 (有效 / 部分有效 / 无效),AI 能看到"上次对同一个老人这么处理、结果如何"。
- **📇 引用式回答**
  回答必须以 `[E1] [E2]` 形式显式引用证据,证据不足时直接写"证据不足",不臆测。前端可点编号跳转到原始病历照片或档案段落。
- **🃏 结构化任务卡,不是一段话**
  Ollama 直接产出严格 JSON,后端白名单归一化;
  前端渲染成"可打卡清单 + 复测计划 + 禁止事项 + SBAR 交接单",每一项都是可勾选、可入档的动作。
- **🔌 失败不伪造**
  Ollama 挂了,API 真实返回 503;OCR 没装,metadata 明确写 `ocr_status=unavailable`,不会给假结果。

---

## 🧱 系统架构

```
                   ┌────────────────────────────────────────────────┐
 管理端 index.html │                                                │
 护工端 nurse.html │              FastAPI + Uvicorn                 │
                   │    /api/ehr/*    /api/nursing/*    /uploads    │
                   └──────┬──────────────┬──────────────┬───────────┘
                          │              │              │
                          ▼              ▼              ▼
                 ┌────────────────┐ ┌─────────┐ ┌──────────────────┐
                 │  HybridRetriever │ │OCR 服务 │ │  Ollama (本地)    │
                 │  Dense + BM25   │ │RapidOCR │ │  huatuo_o1_7b    │
                 │  + RRF 融合     │ │Tesseract│ │  JSON 任务卡生成 │
                 └──────┬─────────┘ └────┬────┘ └─────────┬────────┘
                        │                │                │
                        ▼                ▼                ▼
                 ┌───────────────────────────────────────────────┐
                 │  ChromaDB (PersistentClient, 本地磁盘)         │
                 │  patient_profile / medical_record_upload /    │
                 │  observation / decision_log                   │
                 └───────────────────────────────────────────────┘

                        Embedding: BAAI/bge-small-zh-v1.5 (CPU 可跑)
```

| 层 | 选型 | 用途 |
|---|---|---|
| Web 框架 | FastAPI 0.115 + Uvicorn 0.32 | REST + SSE + 静态托管 |
| 数据校验 | Pydantic 2.10 | 请求/响应 Schema |
| 向量库 | ChromaDB 0.5 (PersistentClient) | 档案 / 病历 OCR / 观察 / 决策日志 |
| Embedding | sentence-transformers + `BAAI/bge-small-zh-v1.5` | 中文轻量,CPU 可跑 |
| OCR | RapidOCR (ONNX) → Tesseract (chi_sim) 兜底 | 病历照片文字识别,纯本地 |
| LLM | Ollama + `huatuo_o1_7b` | 护理建议 / 任务卡 JSON 生成 |
| 图像 | Pillow | EXIF 纠正 + 对比度增强 |
| 日志 | loguru | 结构化启动 / 请求日志 |

---

## 🚀 快速开始

### 环境要求

| 项 | 最低 | 推荐 |
|---|---|---|
| OS | Linux / macOS / Windows | Ubuntu 22.04 LTS |
| Python | 3.10 | 3.11 |
| 内存 | 16 GB | 32 GB |
| GPU | 不需要 | 可选,NVIDIA ≥ 8GB 显存时更流畅 |

### 三步启动

```bash
# 1. 克隆与安装
git clone https://github.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. 本地大模型 (一次性,首次需联网拉取)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull huatuo_o1_7b

# 3. OCR (Ubuntu,二选一或都装)
sudo apt install -y tesseract-ocr tesseract-ocr-chi-sim
pip install rapidocr_onnxruntime   # 可选,中文识别效果更好

# 启动
uvicorn main:app --host 0.0.0.0 --port 8000
```

访问:

- 管理端: <http://localhost:8000/>
- 护工端: <http://localhost:8000/nurse>
- 健康检查: <http://localhost:8000/health>

> 首次启动会联网下载 `bge-small-zh-v1.5` (约 100 MB) 到 `~/.cache/torch/sentence_transformers/`。
> **下载一次后整台机器即可完全断网运行**。

---

## 🔌 API 一览

所有接口在 `/api/*` 下,默认无鉴权(局域网部署前提);如需外网暴露,请自行加反向代理 + Basic Auth / OAuth。

### EHR 档案管理

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/ehr/patients` | 新增老人档案 |
| `GET`  | `/api/ehr/patients` | 列出所有老人档案 |
| `GET`  | `/api/ehr/patients/{patient_id}` | 查询单个档案 |
| `PUT`  | `/api/ehr/patients/{patient_id}` | 修改档案 |
| `DELETE` | `/api/ehr/patients/{patient_id}` | 删除档案 (含照片 / OCR 文本) |
| `POST` | `/api/ehr/records/upload` | 上传病历照片,自动 OCR + 入向量库 |
| `GET`  | `/api/ehr/records/{patient_id}` | 查询某老人全部病历照片 + OCR 文本 |
| `DELETE` | `/api/ehr/records/{doc_id}` | 删除单份病历照片档案 |

### 护理决策 / 任务卡

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET`  | `/api/nursing/patient/{patient_id}` | 拉取护工端需要的档案摘要 |
| `POST` | `/api/nursing/decision` | RAG 推理 (混合检索 + 引用 + 决策记忆) |
| `POST` | `/api/nursing/decision/stream` | 同上,SSE 流式输出 |
| `POST` | `/api/nursing/optimize_prompt` | 基于病史把口语化描述改写成专业表述 |
| `GET`  | `/api/nursing/decisions?patient_id=...` | 查询决策记忆 |
| `PATCH`| `/api/nursing/decisions/{decision_id}/outcome` | 回填决策结果 (有效 / 部分 / 无效) |

> **示例**:发起一次 RAG 决策
>
> ```bash
> curl -X POST http://localhost:8000/api/nursing/decision \
>   -H 'Content-Type: application/json' \
>   -d '{"patient_id": "p002", "symptom": "今天下午血压 180/110,头痛"}'
> ```

---

## 📁 目录结构

```
.
├── app/
│   ├── core/config.py          # 模型名、路径、超参、Prompt 模板
│   ├── routers/
│   │   ├── ehr.py              # 档案 CRUD + 病历照片上传 + OCR
│   │   └── nursing.py          # RAG 决策 + 任务卡 + 事件闭环
│   └── services/
│       ├── retrieval.py        # 混合检索 (Dense + BM25 + RRF)
│       ├── decision_memory.py  # 决策记忆 + outcome 回填
│       ├── llm_service.py      # Ollama 客户端 (stream / non-stream)
│       └── ocr_service.py      # RapidOCR → Tesseract 兜底
├── static/
│   ├── index.html              # 管理端
│   ├── nurse.html              # 护工端
│   ├── design/                 # 液态玻璃设计系统 (tokens/glass/ui/mobile)
│   ├── pet/                    # 桌宠动画
│   └── sw.js / manifest.json   # PWA 支持
├── scripts/run.sh              # 一键启动
├── main.py                     # FastAPI 入口
└── requirements.txt
```

### 本机数据目录

```
./local_ehr_db/                           # ChromaDB (最关键,需备份)
./local_ehr_uploads/<pid>/photos/         # 病历原图
./local_ehr_uploads/<pid>/ocr/            # OCR 识别文本
./local_nursing_events/events.json        # 护理事件流水
~/.cache/torch/sentence_transformers/     # Embedding 离线缓存
~/.ollama/models/                         # huatuo_o1_7b 本地权重
```

---

## 🏭 生产部署

### 用 systemd 托管 (推荐)

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
journalctl -u zhihuyinban -f   # 实时日志
```

### 离线安装要点

1. 在**联网机**上先跑一次 `SentenceTransformer("BAAI/bge-small-zh-v1.5")`,然后打包 `~/.cache/torch/sentence_transformers/` 整个目录;
2. `pip download -r requirements.txt -d wheels/` 把所有 wheel 打包;
3. 目标机离线安装: `pip install --no-index --find-links=./wheels -r requirements.txt`;
4. 把模型缓存放到同样的 `~/.cache/torch/sentence_transformers/` 路径即可。

### 数据备份

**`local_ehr_db/` 是全部数据的核心,务必每日打包备份到异地。**
建议用 `restic` / `borg` 做增量快照,或者最简单 `tar czf backup-$(date +%F).tgz local_ehr_db/`。

---

## 🗺️ 路线图

- [x] 档案 CRUD + 病历照片 OCR
- [x] 混合检索 (Dense + BM25 + RRF)
- [x] 护理任务卡 (严格 JSON)
- [x] SSE 流式输出
- [x] 决策记忆 + outcome 回填
- [ ] 多机构数据隔离 (tenant_id)
- [ ] 交接单自动排版 PDF 导出
- [ ] 护工端离线 PWA 打包
- [ ] 微调脚本:把本机决策日志反哺到 huatuo 的 LoRA

---

## ⚠️ 边界声明

AI 生成的护理建议**仅供护理参考,不替代医生诊断,不构成处方**。
涉及给药等敏感场景,系统只提示"请负责人核对医嘱",不会直接生成剂量。
遇到严重症状请立即联系医生或启动急救流程。

---

## 📜 License

本项目采用 **[PolyForm Noncommercial License 1.0.0](./LICENSE)** 授权 —— 仅允许**非商业用途**。

- ✅ 允许:个人学习 / 研究、教学、公益、非营利医疗机构与养老机构内部使用、在非商用前提下修改和再分发(需保留本授权)。
- ❌ 不允许:将本项目(或其衍生版本)用于任何商业用途,包括但不限于作为商业产品或 SaaS 服务售卖、提供付费部署/接入、打包进商业软件。
- 📮 **商业授权合作**请联系作者另行签署:[@jiahuacaogoodman-art](https://github.com/jiahuacaogoodman-art)

Copyright © 2026 [jiahuaCao](https://github.com/jiahuacaogoodman-art)

---

<p align="center">
  如果这个项目帮到了你,请给个 ⭐ — 这是我继续写下去的最大动力。
</p>
