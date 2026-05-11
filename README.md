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
  <a href="#-功能总览">功能总览</a> ·
  <a href="#-快速开始">快速开始</a> ·
  <a href="#-配置说明">配置说明</a> ·
  <a href="#-使用指南">使用指南</a> ·
  <a href="#-api-参考">API 参考</a> ·
  <a href="#-系统架构">架构</a> ·
  <a href="#-路线图">路线图</a>
</p>

---

## 🌱 为什么做这个

基层养老院面对的真实矛盾是：

- 老人多、护工人均看护数高，**专业经验很难均质化**；
- 病历碎在纸上、U 盘里、微信群里，**AI 想用却无从入手**；
- 院方最担心"**数据上云 = 合规和责任**"，所以很多云端 AI 方案直接被一票否决。

**智护银伴**的目标：让养老院把"大模型 + RAG"真正用起来，所有档案、照片、AI 决策全部保存在本机磁盘，一台普通服务器 + 局域网即可运行。

---

## ✨ 功能总览

### 核心业务功能

| 模块 | 功能 | 说明 |
|---|---|---|
| **老人档案管理** | 增删改查 | 支持 21 个字段（姓名、年龄、病史、过敏、床位、护理等级等） |
| **病历照片 OCR** | 上传 → 本地识别 → 向量化 | RapidOCR (ONNX) + Tesseract 双引擎，纯本地运行 |
| **AI 护理决策** | 混合检索 + LLM 推理 | Dense + BM25 + RRF 融合，带源类型加权和引用标注 |
| **护理任务卡** | 结构化 JSON 输出 | 可打卡清单 + 复测计划 + 禁止事项 + SBAR 交接单 |
| **决策记忆 (L4)** | 自动写回 + 结果回填 | AI 看得到"上次对同一个老人怎么处理、效果如何" |
| **SSE 流式输出** | 逐 token 推送 | 护工端实时看到生成过程 |
| **提示词优化** | 口语 → 专业表述 | 基于病史自动改写护工描述 |

### 安全与合规

| 模块 | 功能 | 说明 |
|---|---|---|
| **用户身份认证** | 多用户 + 多 API Key + 角色 | admin / nurse / caregiver 三种角色 |
| **PII 字段加密** | Fernet 对称加密 10 个高敏字段 | 姓名、身份证、床位、联系人、过敏史等写入前自动加密 |
| **操作审计日志** | 全部写操作留痕 | 谁在什么时间对哪个老人做了什么修改，带 diff |
| **审计防泄密** | diff 中 PII 自动脱敏 | 审计日志不含明文也不含密文，只标记"有变化" |
| **占位符写回防御** | 密钥缺失时拒绝写入 | 防止解密失败的占位符污染数据库 |

### 运维可观测

| 端点 | 返回 | 用途 |
|---|---|---|
| `GET /health` | `pii_encryption_enabled` + `auth_mode` + 服务状态 | systemd / k8s 探针 + 监控系统 |
| `GET /api/ehr/audit` | 按时间/操作/患者筛选 | 运维审计（admin 专属） |

---

## 🚀 快速开始

### 环境要求

| 项 | 最低 | 推荐 |
|---|---|---|
| OS | Linux / macOS / Windows | Ubuntu 22.04 LTS |
| Python | 3.10 | 3.12 |
| 内存 | 16 GB | 32 GB |
| GPU | 不需要 | 可选，NVIDIA >= 8GB 显存时更流畅 |

### 安装与启动

```bash
# 1. 克隆
git clone https://github.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban

# 2. 创建虚拟环境并安装依赖
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. 本地大模型：安装 Ollama + 一键装 HuatuoGPT-o1-7B
curl -fsSL https://ollama.com/install.sh | sh
bash scripts/setup_model.sh   # 详见下方【本地大模型配置】

# 4. OCR（Ubuntu，二选一或都装）
sudo apt install -y tesseract-ocr tesseract-ocr-chi-sim
pip install rapidocr_onnxruntime   # 可选，中文效果更好

# 5. 配置环境变量
cp .env.example .env
# 编辑 .env，设置 AUTH_TOKEN 和 PII_ENCRYPTION_KEY（见下方配置说明）

# 6. 启动
uvicorn main:app --host 0.0.0.0 --port 8000
```

启动后访问：

| 页面 | 地址 |
|---|---|
| 管理端 | http://localhost:8000/ |
| 护工端 | http://localhost:8000/nurse |
| 健康检查 | http://localhost:8000/health |

> 首次启动会下载 `bge-small-zh-v1.5`（约 100MB）到 `~/.cache/torch/sentence_transformers/`，下载一次后即可完全断网运行。

---

## 🤖 本地大模型配置（HuatuoGPT-o1-7B）

项目默认用的是 **[HuatuoGPT-o1-7B](https://huggingface.co/FreedomIntelligence/HuatuoGPT-o1-7B)**
（中文医疗大模型，约 8 GB），代码里叫 `huatuo_o1_7b`。这个名字**只是本项目的本地别名**，
Ollama 官方 Registry 上没有这一条，直接 `ollama pull huatuo_o1_7b` 会 404。

### 一键安装（推荐）

确保 [Ollama](https://ollama.com/) 已装好并在运行，然后执行：

```bash
bash scripts/setup_model.sh
```

脚本做的事：检查 Ollama 在不在 → 从 [Ollama 社区包](https://ollama.com/cliu/HuatuoGPT-o1-7B) 拉 8 GB
权重 → 给它起本项目用的别名 `huatuo_o1_7b` → 跑一次冒烟测试确认能回话。

执行完应该看到：

```
[ ok ] ollama 已安装
[ ok ] ollama 服务已就绪（:11434）
[ ok ] 已有 cliu/HuatuoGPT-o1-7B:latest，跳过下载
[ ok ] 已有别名 huatuo_o1_7b，跳过
[ ok ] 模型回话：你好！我是 HuatuoGPT...
=== 全部搞定 ===
```

脚本**重复跑也安全**，断网重连后再执行一次即可。

### 硬件带不动 7B？换小模型（30 秒搞定）

16 GB 以下内存跑 HuatuoGPT-o1-7B 会 OOM，改用 Qwen 2.5 的 3B 版：

```bash
ollama pull qwen2.5:3b
echo 'OLLAMA_MODEL_NAME=qwen2.5:3b' >> .env
```

> 注意：非医疗专用模型生成"护理任务卡"时偶尔会吐出多余文字，导致 JSON 解析失败。
> 项目里有重试兜底，但临床场景强烈建议上 HuatuoGPT-o1-7B。

### 验证装好了

```bash
# 装完检查一下别名存在
ollama list | grep huatuo_o1_7b

# 让模型回一句话（冷启动首次会慢 5–30 秒）
ollama run huatuo_o1_7b "高血压老人头晕该怎么处理？"
```

### 常见坑

| 现象 | 原因 | 解决 |
|---|---|---|
| 脚本提示"未检测到 ollama" | 没装 Ollama | `curl -fsSL https://ollama.com/install.sh \| sh` |
| 脚本提示"服务未响应 :11434" | Ollama 进程没起 | Linux: `sudo systemctl start ollama`；macOS: 打开 Ollama.app |
| `pull` 卡住 / 超时 | 国内连不上 Registry | 挂代理再重跑 `bash scripts/setup_model.sh` |
| 护理接口 503 "本地大模型不可用" | 项目启动时 Ollama 没就绪 | `ollama list` 确认 `huatuo_o1_7b:latest` 在；重启后端 |
| 首次推理特别慢（10 秒+） | 冷启动加载权重 | 正常现象；想预热跑 `ollama run huatuo_o1_7b ""` |
| OOM 内存炸 | 16 GB 机器跑 7B 偏紧 | 走上面"硬件带不动 7B"的小模型方案 |

<details>
<summary><b>高级：断网部署 / 自建量化版本（从 GGUF 导入）</b></summary>

离线机器 / 想选特定量化（Q4_K_M 4.7 GB 平衡 / Q6_K 6.25 GB 高精度 / Q8_0 8.10 GB 最高）时，
跳过一键脚本，从 HuggingFace 直接导入 GGUF：

```bash
# 1. 下载 GGUF（huggingface-cli 比 wget 稳，支持断点续传）
pip install -U "huggingface_hub[cli]"
huggingface-cli download bartowski/HuatuoGPT-o1-7B-GGUF \
  --include "HuatuoGPT-o1-7B-Q4_K_M.gguf" \
  --local-dir ./

# 2. 写 Modelfile（stop token 千万别漏，否则模型会假装自己是对方继续发言）
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

# 3. 导入并注册为本项目所需的别名
ollama create huatuo_o1_7b -f Modelfile

# 4. 验证
ollama list | grep huatuo_o1_7b
```

**量化怎么选**：

| 机器 | 推荐 | 体积 |
|---|---|---|
| 16 GB RAM 纯 CPU | `Q4_K_M` | 4.68 GB |
| 32 GB RAM 或 8 GB VRAM | `Q6_K` | 6.25 GB |
| 16 GB+ VRAM GPU | `Q8_0` | 8.10 GB |
| Apple Silicon (M1/M2/M3) | `IQ4_NL` | 4.44 GB |

全部量化档位见 [bartowski/HuatuoGPT-o1-7B-GGUF](https://huggingface.co/bartowski/HuatuoGPT-o1-7B-GGUF/tree/main)。

</details>

---

## ⚙️ 配置说明

所有配置通过环境变量或 `.env` 文件设置：

### 必须配置（生产环境）

| 变量 | 用途 | 生成方式 |
|---|---|---|
| `AUTH_TOKEN` | 首次启动的管理员 bootstrap token | `openssl rand -hex 32` |
| `PII_ENCRYPTION_KEY` | PII 字段加密密钥 (Fernet) | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

### 可选配置

| 变量 | 默认值 | 说明 |
|---|---|---|
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `8000` | 监听端口 |
| `RELOAD` | `0` | 热重载（开发用，生产禁用） |
| `MAX_UPLOAD_SIZE_MB` | `15` | 单张病历照片大小上限 |

### 鉴权模式自动切换

系统启动时根据配置自动选择模式：

| 条件 | 模式 | 说明 |
|---|---|---|
| UserStore 有用户 | `user_store` | 正常运行（bootstrap 后即进入此模式） |
| UserStore 空 + AUTH_TOKEN 非空 | `legacy_token` | 首次启动瞬间，bootstrap 创建 admin 后自动切为 user_store |
| 两者都空 | `disabled` | 仅限开发测试，所有接口无需 token |

---

## 📖 使用指南

### 1. 首次部署：创建管理员

```bash
# 生成随机 token 写入 .env
echo "AUTH_TOKEN=$(openssl rand -hex 32)" >> .env

# 启动服务 → admin 用户自动创建，AUTH_TOKEN 成为其 API Key
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 2. 创建护工账号

```bash
# 用 admin token 创建护工
curl -X POST http://localhost:8000/api/auth/users \
  -H "X-Auth-Token: YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "wang_nurse", "display_name": "王护士", "role": "nurse"}'
```

### 3. 为护工签发 API Key

```bash
# token 仅此次返回，请立即保存！
curl -X POST http://localhost:8000/api/auth/tokens \
  -H "X-Auth-Token: YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "usr_xxxxxxxx", "label": "护工端平板"}'
```

### 4. 录入老人档案

```bash
curl -X POST http://localhost:8000/api/ehr/patients \
  -H "X-Auth-Token: NURSE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "patient_id": "p001",
    "name": "张奶奶",
    "age": 82,
    "gender": "女",
    "bed_number": "A-205",
    "care_level": "二级",
    "medical_history": "高血压20年、2型糖尿病15年，长期服用缬沙坦+二甲双胍",
    "allergy": "青霉素",
    "emergency_contact": "张明",
    "emergency_phone": "13800138000",
    "emergency_relation": "儿子"
  }'
```

### 5. 上传病历照片（自动 OCR）

```bash
curl -X POST http://localhost:8000/api/ehr/records/upload \
  -H "X-Auth-Token: NURSE_TOKEN" \
  -F "patient_id=p001" \
  -F "record_type=出院小结" \
  -F "files=@/path/to/discharge_summary.jpg"
```

### 6. AI 护理决策（核心功能）

```bash
# 普通模式
curl -X POST http://localhost:8000/api/nursing/decision \
  -H "X-Auth-Token: NURSE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"patient_id": "p001", "symptom": "今天下午血压180/110，头痛头晕，手心出汗"}'
```

返回结构包含：
- `llm_advice`: AI 生成的护理建议（带 `[E1] [E2]` 引用标注）
- `evidence`: 检索到的证据列表（来源、片段、可跳转）
- `memory`: 该患者近期决策回忆
- `decision_id`: 本次决策 ID（后续回填结果用）

### 7. 流式输出（SSE，护工端实时显示）

```bash
curl -N -X POST http://localhost:8000/api/nursing/decision/stream \
  -H "X-Auth-Token: NURSE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"patient_id": "p001", "symptom": "餐前血糖3.2，手抖冒汗"}'
```

SSE 事件流：
1. `event: context` — 检索到的病史上下文
2. `event: evidence` — 结构化证据 + 决策记忆
3. `event: token` — 逐 token 生成内容
4. `event: done` — 完成（含 decision_id）

### 8. 回填决策执行结果（L4 闭环）

```bash
curl -X PATCH http://localhost:8000/api/nursing/decisions/dec_20260511_143000_abc123/outcome \
  -H "X-Auth-Token: NURSE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "outcome_status": "effective",
    "note": "复测血糖5.6，症状缓解",
    "recorded_by": "王护士"
  }'
```

`outcome_status` 可选值：`effective` / `partial` / `ineffective`

下次 AI 检索同一患者时，会看到"上次建议 X，实际效果 Y"，避免重复无效方案。

### 9. 查看审计日志（管理员专属）

```bash
curl "http://localhost:8000/api/ehr/audit?patient_id=p001&limit=20" \
  -H "X-Auth-Token: YOUR_ADMIN_TOKEN"
```

---

## 🖥️ 网页端操作指南

除了命令行，所有功能都可以在浏览器中完成。

### 登录（管理端 & 护工端通用）

1. 打开管理端 `http://host:8000/` 或护工端 `http://host:8000/nurse`
2. 左侧栏（管理端）/ 顶栏（护工端）有一个 **Token 输入框**
3. 把你的 API Token 粘贴进去 → 下方显示 `● 用户名 (角色)` 表示登录成功
4. Token 会自动保存在浏览器 localStorage，刷新页面不需要重新输入

> **提示**：如果是第一次部署，Token 就是你 `.env` 里的 `AUTH_TOKEN` 值。

### 管理端（7 个 Tab）

| Tab | 用法 |
|---|---|
| **录入档案** | 填写老人信息 → 点"保存档案"。编号和姓名必填，其他选填 |
| **档案管理** | 查看所有老人列表，支持搜索（姓名/编号/床位/病史/过敏等任意字段模糊匹配）。点笔图标编辑，点垃圾桶删除 |
| **病历上传** | 先选老人（或填编号）→ 选照片 → "上传并识别文字"。识别结果自动进入向量库供 AI 检索 |
| **AI 护理建议** | 选老人 → 描述症状（可点快捷标签如"头晕""跌倒"一键添加）→ "获取 AI 护理建议"。生成过程逐字显示，右侧面板展示证据来源和过往决策记忆 |
| **操作记录** | 本次会话内的操作流水（新增/编辑/删除/AI建议），支持按类型和关键词筛选 |
| **审计日志** | 调用后端 `/api/ehr/audit`，展示全量写操作留痕。可按操作类型（新建/修改/删除/上传）和 patient_id 筛选。**需要 admin Token** |
| **用户管理** | 创建用户（填用户名 + 选角色）、查看已有用户列表、为用户签发 Token（弹窗一次性展示）。**需要 admin Token** |

### 护工端

| 区域 | 用法 |
|---|---|
| **顶栏** | Token 输入框 + 当前身份显示 |
| **选老人** | 下拉列表选择已建档老人，页面展示该老人的基本信息（年龄、床位、护理等级、过敏、病史摘要） |
| **描述症状** | 口语化描述即可（如"今天早上说头晕，血压偏高"） |
| **生成任务卡** | 点击后 AI 根据老人档案 + 症状生成结构化护理任务卡，包含：可打卡任务列表、复测计划、禁止事项、SBAR 交接单 |
| **打卡** | 每条任务可点"完成" / 填写测量值 / 记录异常。完成后状态自动更新 |
| **结果回填** | AI 建议执行完后，可标记"有效 / 部分有效 / 无效"。下次 AI 对同一老人给建议时会参考历史效果 |

### 典型工作流（护工日常）

```
早班交接 → 打开护工端 → 选老人 → 看昨日未完成事项
  ↓
发现异常 → 输入"餐后说胸闷，出冷汗" → 生成任务卡
  ↓
按任务卡操作：测血压 / 血氧 / 通知护士 → 逐条打卡
  ↓
30分钟后复测 → 症状缓解 → 回填"有效"
  ↓
下次同一老人类似症状 → AI 自动参考本次处理经验
```

---

## 🔌 API 参考

所有接口在 `/api/*` 下，请求头需带 `X-Auth-Token`（或 query param `?token=xxx`）。

### 认证管理 `/api/auth/*`

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `GET` | `/api/auth/me` | 任意用户 | 查看当前登录身份 |
| `GET` | `/api/auth/users` | admin | 列出所有用户 |
| `POST` | `/api/auth/users` | admin | 创建用户 |
| `POST` | `/api/auth/tokens` | admin | 为用户签发 API Key（token 仅返回一次） |
| `DELETE` | `/api/auth/tokens/{token_id}` | admin | 吊销某个 API Key |

### 档案管理 `/api/ehr/*`

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/ehr/patients` | 新增老人档案 |
| `GET` | `/api/ehr/patients` | 列出所有老人 |
| `GET` | `/api/ehr/patients/{patient_id}` | 查询单个档案 |
| `PUT` | `/api/ehr/patients/{patient_id}` | 修改档案 |
| `DELETE` | `/api/ehr/patients/{patient_id}` | 删除档案（含照片/OCR） |
| `POST` | `/api/ehr/records/upload` | 上传病历照片（自动 OCR + 入向量库） |
| `GET` | `/api/ehr/records/{patient_id}` | 查询某老人全部病历照片 |
| `DELETE` | `/api/ehr/records/{doc_id}` | 删除单份病历 |
| `GET` | `/api/ehr/audit` | 查询操作审计日志（admin） |

### 护理决策 `/api/nursing/*`

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/nursing/patient/{patient_id}` | 护工端档案摘要 |
| `POST` | `/api/nursing/decision` | RAG 推理（混合检索 + 引用 + 决策记忆） |
| `POST` | `/api/nursing/decision/stream` | 同上，SSE 流式输出 |
| `POST` | `/api/nursing/optimize_prompt` | 口语描述 → 专业表述 |
| `GET` | `/api/nursing/decisions?patient_id=...` | 查询决策历史 |
| `GET` | `/api/nursing/decisions/{decision_id}` | 查询单条决策 |
| `PATCH` | `/api/nursing/decisions/{decision_id}/outcome` | 回填决策执行结果 |
| `POST` | `/api/nursing/task_card/generate` | 生成 AI 护理任务卡 |
| `POST` | `/api/nursing/task_card/{event_id}/complete` | 打卡完成任务项 |
| `GET` | `/api/nursing/events` | 查询护理事件列表 |
| `GET` | `/api/nursing/events/{event_id}` | 查询单个护理事件 |

---

## 🧱 系统架构

```
                   ┌────────────────────────────────────────────────┐
 管理端 index.html │                                                │
 护工端 nurse.html │          FastAPI + Uvicorn + Auth              │
                   │  /api/auth/*  /api/ehr/*  /api/nursing/*       │
                   └──────┬──────────────┬──────────────┬───────────┘
                          │              │              │
                          ▼              ▼              ▼
                 ┌────────────────┐ ┌─────────┐ ┌──────────────────┐
                 │ HybridRetriever│ │OCR 服务  │ │  Ollama (本地)    │
                 │ Dense + BM25  │ │RapidOCR │ │  huatuo_o1_7b    │
                 │ + RRF + 加权  │ │Tesseract│ │  JSON 任务卡     │
                 └──────┬─────────┘ └────┬────┘ └─────────┬────────┘
                        │                │                │
                        ▼                ▼                ▼
            ┌───────────────────────────────────────────────────────┐
            │  ChromaDB (本地磁盘)                                   │
            │  patient_profile / medical_record / decision_log      │
            ├───────────────────────────────────────────────────────┤
            │  SQLite (WAL 模式)                                    │
            │  local_auth/users.db     ← 用户 + API Key            │
            │  local_audit_log/audit.db ← 操作审计                  │
            │  local_nursing_events/events.db ← 护理事件            │
            └───────────────────────────────────────────────────────┘
                        ↑
              PII 加密层 (Fernet) — 写入前加密 / 读出后解密
```

### 技术栈

| 层 | 选型 | 用途 |
|---|---|---|
| Web 框架 | FastAPI + Uvicorn | REST + SSE + 静态托管 |
| 数据校验 | Pydantic v2 | 请求/响应 Schema |
| 向量库 | ChromaDB (PersistentClient) | 档案 / 病历 / 决策日志 |
| Embedding | `BAAI/bge-small-zh-v1.5` | 中文轻量，CPU 可跑 |
| OCR | RapidOCR (ONNX) → Tesseract 兜底 | 病历照片文字识别 |
| LLM | Ollama + `huatuo_o1_7b` | 护理建议 / 任务卡生成 |
| 加密 | cryptography (Fernet) | PII 字段透明加密 |
| 存储 | SQLite WAL | 用户/审计/事件持久化 |
| 日志 | loguru | 结构化日志 |

---

## 📁 目录结构

```
.
├── app/
│   ├── core/config.py              # 模型名、路径、超参、Prompt 模板
│   ├── middleware/auth.py          # 三模式鉴权中间件
│   ├── models/
│   │   ├── schemas.py             # 业务请求/响应 Schema
│   │   └── auth_schemas.py        # 认证相关 Schema
│   ├── routers/
│   │   ├── auth.py                # 用户 + Token 管理
│   │   ├── ehr.py                 # 档案 CRUD + 病历上传 + OCR + 审计
│   │   └── nursing.py             # RAG 决策 + 任务卡 + 事件闭环
│   └── services/
│       ├── audit_log.py           # 操作审计 (SQLite)
│       ├── decision_memory.py     # 决策记忆 + outcome 回填
│       ├── event_store.py         # 护理事件持久化 (SQLite)
│       ├── llm_service.py         # Ollama 客户端 (普通 + 流式)
│       ├── ocr_service.py         # RapidOCR → Tesseract 兜底
│       ├── pii_crypto.py          # PII 字段 Fernet 加密/解密
│       ├── protocol_loader.py     # 护理协议模板热加载
│       ├── retrieval.py           # 混合检索 (Dense + BM25 + RRF)
│       └── user_store.py          # 用户 + API Key 存储
├── data/protocols.yaml             # 护理协议模板（可热编辑）
├── static/                         # 前端页面 + PWA
├── tests/                          # 测试套件 (65 cases)
├── main.py                         # 应用入口
├── requirements.txt
├── Dockerfile
└── .env.example                    # 环境变量模板
```

### 本机数据目录

```
./local_ehr_db/                     # ChromaDB 向量库（最关键，需备份）
./local_ehr_uploads/<pid>/photos/   # 病历原图
./local_ehr_uploads/<pid>/ocr/      # OCR 识别文本
./local_auth/users.db               # 用户 + API Key
./local_audit_log/audit.db          # 操作审计日志
./local_nursing_events/events.db    # 护理事件
~/.cache/torch/sentence_transformers/  # Embedding 模型缓存
~/.ollama/models/                   # LLM 权重
```

---

## 🏭 生产部署

### Docker

```bash
docker build -t zhihu-yinban .
docker run -d --name yinban \
  -p 8000:8000 \
  -v ./data:/app/local_ehr_db \
  -v ./uploads:/app/local_ehr_uploads \
  -v ./auth:/app/local_auth \
  -e AUTH_TOKEN=$(openssl rand -hex 32) \
  -e PII_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  zhihu-yinban
```

### systemd

```ini
[Unit]
Description=ZhiHu YinBan Backend
After=network.target

[Service]
User=zhihu
WorkingDirectory=/opt/zhihuyinban
EnvironmentFile=/opt/zhihuyinban/.env
ExecStart=/opt/zhihuyinban/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 数据备份

**务必每日备份以下目录**：
- `local_ehr_db/` — 全部向量数据
- `local_auth/` — 用户身份
- `local_audit_log/` — 审计留痕
- `local_ehr_uploads/` — 病历原图

```bash
tar czf backup-$(date +%F).tgz local_ehr_db/ local_auth/ local_audit_log/ local_ehr_uploads/
```

---

## 🗺️ 路线图

- [x] 档案 CRUD + 病历照片 OCR
- [x] 混合检索 (Dense + BM25 + RRF)
- [x] 护理任务卡 (严格 JSON + 可打卡)
- [x] SSE 流式输出
- [x] 决策记忆 + outcome 回填 (L4 闭环)
- [x] 用户身份 + 多 API Key + 角色
- [x] 操作审计日志 (全写操作留痕)
- [x] PII 字段加密 (10 字段 Fernet)
- [x] 审计 diff 防泄密
- [x] 护理事件 SQLite 持久化
- [x] 护理协议模板热加载
- [ ] 多机构数据隔离 (tenant_id)
- [ ] 交接单 PDF 导出
- [ ] 护工端离线 PWA 打包
- [ ] 密钥轮换自动化
- [ ] 微调脚本：决策日志 → huatuo LoRA

---

## ⚠️ 边界声明

AI 生成的护理建议**仅供护理参考，不替代医生诊断，不构成处方**。
涉及给药等敏感场景，系统只提示"请负责人核对医嘱"，不会直接生成剂量。
遇到严重症状请立即联系医生或启动急救流程。

---

## 📜 License

本项目采用 **[PolyForm Noncommercial License 1.0.0](./LICENSE)** 授权 —— 仅允许**非商业用途**。

- ✅ 允许：个人学习 / 研究、教学、公益、非营利医疗机构与养老机构内部使用
- ❌ 不允许：将本项目用于任何商业用途
- 📮 **商业授权合作**请联系：[@jiahuacaogoodman-art](https://github.com/jiahuacaogoodman-art)

Copyright (c) 2026 [jiahuaCao](https://github.com/jiahuacaogoodman-art)

---

<p align="center">
  如果这个项目帮到了你，请给个 ⭐ — 这是我继续写下去的最大动力。
</p>
