# 智护银伴

<p align="center">
  一个为养老院设计的本地化 AI 护理辅助系统。<br>
  所有老人档案、病历照片、护理记录全部保存在本机，<br>
  断网也能给出可落地的护理建议。
</p>

<p align="center">
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="fastapi" src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green">
</p>

---

## 1. 它做什么

| 面向 | 能做的事 |
|---|---|
| **管理端**（护士站） | 录入/编辑老人档案、上传病历照片、自动识别文字、查询 AI 护理建议、查看过往决策 |
| **护工端**（平板/手机） | 选择老人、描述目前情况、一键生成可打卡的护理任务卡、记录异常、一键归档 + 生成 SBAR 交接单 |

**核心承诺**：数据不出院、不出局域网、不调用任何云端 API。

---

## 2. 技术路线

```
前端静态页（管理端 + 护工端）
      │ HTTP / SSE
      ▼
FastAPI + Uvicorn
      │
      ├── 本地 OCR     ── RapidOCR(ONNX) → Tesseract(chi_sim) 兜底
      ├── 本地 向量库  ── ChromaDB（磁盘持久化）
      ├── 本地 Embedding ── BAAI/bge-small-zh-v1.5（CPU 可跑）
      └── 本地 LLM     ── Ollama + huatuo_o1_7b
```

| 层 | 选型 | 用途 |
|---|---|---|
| Web | FastAPI 0.115 + Uvicorn 0.32 | REST API + 静态托管 |
| 数据校验 | Pydantic 2.10 | 请求/响应 Schema |
| 向量库 | ChromaDB 0.5（PersistentClient） | 档案、病历 OCR、决策日志 |
| Embedding | sentence-transformers + bge-small-zh-v1.5 | 中文轻量 |
| OCR | RapidOCR → Tesseract | 病历照片文字识别 |
| LLM | Ollama / huatuo_o1_7b | 生成护理建议 / 任务卡 JSON |
| 图像预处理 | Pillow | EXIF 纠正 + 对比增强 |
| 日志 | loguru | 启动/请求日志 |

---

## 3. 关键算法

这个项目真正用心的地方不是"调 LLM"，而是**检索质量**和**决策可追溯**。

### 混合检索（`app/services/retrieval.py`）

- **稠密检索**：bge-small-zh 向量 → ChromaDB `query` + `where patient_id`
- **稀疏检索**：自研字符 bi-gram BM25，纯 Python、无 jieba 依赖 —— 针对中文药名/病名多是 2–4 字短词的特点
- **融合**：RRF（Reciprocal Rank Fusion, k=60）
- **源类型加权**：`档案(1.0) > 病历上传(0.95) > 观察记录(0.90) > 历史决策(0.85)`
- **输出**：带编号的 Evidence 列表（E1, E2...），前端可点击跳转

### 决策记忆

- 每次 AI 决策 → 写回同一个 Chroma collection，类型为 `decision_log`
- 下次检索时，过去的决策会作为一类 evidence 被自然召回
- 支持 outcome 回填（有效 / 无效 / 部分有效）→ 决策文本更新 + 重新 embedding
- 下次遇到"同样的老人 + 同样的症状"，AI 能看到"上次这样处理了，结果是这样"

### 任务卡生成

- Ollama 直接生成严格 JSON（不是关键词模板）
- 后端校验、归一化、白名单过滤
- Ollama 不可用会真实返回 503，不伪造结果

---

## 4. 目录结构

```
.
├── app/
│   ├── core/config.py          # 模型名、路径、超参
│   ├── models/schemas.py       # Pydantic Schema
│   ├── routers/
│   │   ├── ehr.py              # 档案/病历上传
│   │   └── nursing.py          # 护理建议 / 任务卡 / 事件
│   └── services/
│       ├── retrieval.py        # 混合检索
│       ├── decision_memory.py  # 决策记忆
│       ├── llm_service.py      # Ollama 客户端
│       └── ocr_service.py      # 本地 OCR
├── static/
│   ├── index.html              # 管理端
│   ├── nurse.html              # 护工端
│   └── design/                 # 液态玻璃设计系统（tokens/glass/ui）
├── scripts/run.sh              # 一键启动
├── main.py                     # FastAPI 入口
├── requirements.txt
├── DEPLOYMENT.md               # systemd 部署
└── UI_TREND_DESIGN_NOTES.md    # 视觉设计规范
```

---

## 5. 快速开始

### 依赖

- Python 3.10+
- [Ollama](https://ollama.com/)（跑本地 LLM）
- Tesseract + `chi_sim` 或 `rapidocr_onnxruntime`（二选一）

### 安装

```bash
git clone https://github.com/<你>/<本仓库>.git
cd <本仓库>

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 本地大模型
ollama pull huatuo_o1_7b

# OCR（Ubuntu）
sudo apt install -y tesseract-ocr tesseract-ocr-chi-sim
pip install rapidocr_onnxruntime   # 可选，中文识别更好
```

### 启动

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

打开：

- 管理端：<http://localhost:8000/>
- 护工端：<http://localhost:8000/nurse>

首次启动会联网下载 `bge-small-zh-v1.5`（约 100MB）到 `~/.cache/torch/sentence_transformers/`。下载一次后即可完全断网运行。

---

## 6. 数据目录

```
./local_ehr_db/                           # ChromaDB（最关键，需备份）
./local_ehr_uploads/<pid>/photos/         # 病历原图
./local_ehr_uploads/<pid>/ocr/            # OCR 识别文本
~/.cache/torch/sentence_transformers/     # Embedding 离线缓存
~/.ollama/models/                         # huatuo_o1_7b 本地权重
```

---

## 7. 视觉

前端采用液态玻璃设计，薄荷青主色，清新柔和。

- 彩色 mesh 背景 + 浮动光斑
- `conic-gradient` 彩色 rim 描边
- 顶部 specular + 左上 light-catch + 底部色泄
- 所有原件（按钮、输入、chip、dialog、toast）都有玻璃质感

设计系统文件：
- `static/design/tokens.css` — 色彩、字号、间距、半径、阴影
- `static/design/glass.css` — 玻璃卡片、深色 nav 玻璃、浮动 orb
- `static/design/ui.css` — 按钮、输入、标签、对话、证据面板

### 7.1 外部资源（开源引入，不闭门造车）

| 资源 | 用处 | License | 加载方式 |
|---|---|---|---|
| [Lucide Icons](https://lucide.dev) | 1400+ 线性图标库，替代手搓 SVG | ISC | CDN + 本地 40 个 fallback |
| [GSAP 3.12](https://gsap.com) | 卡片入场、数字滚动、ScrollTrigger 滚入动效 | Standard (免费) | CDN |
| [Lottie Player](https://lottiefiles.com) | hero 区矢量动画 | MIT | CDN |
| [Hero Patterns](https://heropatterns.com) | 玻璃背景的细密点阵纹理 | CC BY 4.0 | 内联 SVG |
| [Inter + Noto Serif SC](https://fonts.google.com) | 主力字体 + 中文显示字体 | OFL | Google Fonts CDN |

所有 CDN 带 **6s 超时兜底**，在 `static/design/vendors.js` 里按需懒加载，加载失败会自动降级为 CSS 原生动画，不阻塞业务。

**离线部署**：执行一次 `./scripts/fetch_vendors.sh`（可选，见下方）把 CDN 资源下载到 `static/vendor/`，然后改引用路径即可完全离线。

---

## 8. 边界声明

AI 生成的护理建议**仅供护理参考，不替代医生诊断，不构成处方**。
涉及给药等敏感场景，系统只提示"请负责人核对医嘱"，不会直接生成剂量。
遇到严重症状请立即联系医生或启动急救流程。

---

## License

MIT
