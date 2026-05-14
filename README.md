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

### 一键部署（按系统选一种，3 行搞定）

只需要装了 Docker，其余全自动——密钥生成、模型下载、GPU 检测、服务启动。

#### 🐧 Linux / 🍎 macOS

```bash
git clone https://github.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban
chmod +x scripts/setup.sh && ./scripts/setup.sh
```

#### 🪟 Windows（PowerShell，无需 WSL）

```powershell
git clone https://github.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

> Windows 用户首次运行可能需要：
> 1. **没装 Docker？** 脚本会自动调用 `winget install Docker.DockerDesktop`
> 2. **PowerShell 执行被拦？** 直接用上面的 `-ExecutionPolicy Bypass` 一次性绕过
> 3. **想先体检一下环境？** 运行 `.\scripts\preflight.ps1`，会逐项告诉你哪里需要修

跟着向导按回车，约 10 分钟后看到 `部署成功！` + 管理员 Token，打开浏览器即可使用。

| 页面 | 地址 |
|---|---|
| 管理端 | http://localhost:8000/ |
| 护工端 | http://localhost:8000/nurse |
| 健康检查 | http://localhost:8000/health |

---

### 🇨🇳 国内网络部署（无梯子，3 行搞定）

#### 🐧 Linux / 🍎 macOS

```bash
git clone https://gitee.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban
chmod +x scripts/setup-cn.sh && ./scripts/setup-cn.sh
```

#### 🪟 Windows

```powershell
git clone https://gitee.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban
powershell -ExecutionPolicy Bypass -File .\scripts\setup-cn.ps1
```

全自动：Gitee 克隆 + 清华 APT 镜像 + hf-mirror.com + 生成密钥 + 启动服务。LLM 后端自由选择——本地 Ollama 或远程 API（DeepSeek / 智谱 / vLLM），编辑 `.env` 中 `LLM_PROVIDER` 即可切换。

> **Docker Hub 慢？**
> - Linux: 编辑 `/etc/docker/daemon.json` 加 `{"registry-mirrors":["https://docker.mirrors.ustc.edu.cn"]}`
> - Windows/Mac: Docker Desktop → Settings → Docker Engine → 同上 JSON → Apply & Restart

---

### 手动安装（开发者 / 不用 Docker）

<details>
<summary>展开手动安装步骤（Linux / macOS）</summary>

#### 环境要求

| 项 | 最低 | 推荐 |
|---|---|---|
| OS | Linux / macOS / Windows | Ubuntu 22.04 LTS |
| Python | 3.10 | 3.12 |
| 内存 | 16 GB | 32 GB |
| GPU | 不需要 | 可选，NVIDIA >= 8GB 显存时更流畅 |

#### 步骤

```bash
# 1. 克隆
git clone https://github.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban

# 2. 创建虚拟环境并安装依赖
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. 本地大模型：安装 Ollama + 拉 HuatuoGPT-o1-7B（详见下方【本地大模型配置】）
curl -fsSL https://ollama.com/install.sh | sh
# 不能直接 `ollama pull huatuo_o1_7b` —— 这个名字是本项目的本地别名，
# 官方 Registry 没有这一条。具体怎么拿到权重，请看下一节。

# 4. OCR（Ubuntu，二选一或都装）
sudo apt install -y tesseract-ocr tesseract-ocr-chi-sim
pip install rapidocr_onnxruntime   # 可选，中文效果更好

# 5. 配置环境变量
cp .env.example .env
# 编辑 .env，设置 AUTH_TOKEN 和 PII_ENCRYPTION_KEY（见下方配置说明）

# 6. 启动
#    main.py 会通过 python-dotenv 自动加载项目根目录下的 .env，
#    所以裸机直接 uvicorn 启动也能读到 AUTH_TOKEN / PII_ENCRYPTION_KEY 等。
#    （已显式 export 的环境变量优先于 .env，systemd / docker 注入永远赢。）
uvicorn main:app --host 0.0.0.0 --port 8000
```

> 首次启动会下载 `bge-small-zh-v1.5`（约 100MB）到 `~/.cache/torch/sentence_transformers/`，下载一次后即可完全断网运行。

</details>

<details>
<summary>展开手动安装步骤（Windows，不用 Docker）</summary>

#### 1. 装 Python（>=3.10）

```powershell
# 推荐用 winget（Win10 1809+ 内置）
winget install -e --id Python.Python.3.12

# 验证
python --version
```

或从官网下载：https://www.python.org/downloads/windows/ —— 安装时**务必勾上 "Add Python to PATH"**。

#### 2. 装 Tesseract OCR（含中文语言包）

```powershell
# 推荐 winget
winget install -e --id UB-Mannheim.TesseractOCR
# 默认装到 C:\Program Files\Tesseract-OCR

# 添加到 PATH（管理员 PowerShell）
[Environment]::SetEnvironmentVariable(
    'PATH',
    [Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';C:\Program Files\Tesseract-OCR',
    'Machine'
)
# 重开 PowerShell 后验证
tesseract --version
tesseract --list-langs   # 应包含 chi_sim
```

如果 `--list-langs` 没看到 `chi_sim`，去 [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) 下载 `chi_sim.traineddata`，丢进 `C:\Program Files\Tesseract-OCR\tessdata\`。

#### 3. 装 Ollama for Windows

```powershell
winget install -e --id Ollama.Ollama
# 或下载安装器：https://ollama.com/download/windows
```

装完默认会注册成 Windows 服务自动启动，监听 `http://localhost:11434`。

#### 4. 克隆项目 + 装依赖

```powershell
git clone https://github.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban

# 创建虚拟环境
python -m venv venv

# 激活（PowerShell）
.\venv\Scripts\Activate.ps1
# 如果报错 "无法加载文件...因为在此系统上禁止运行脚本"，临时绕过：
#   Set-ExecutionPolicy -Scope Process Bypass

# 装依赖
pip install -r requirements.txt
```

#### 5. 拉 HuatuoGPT-o1-7B 模型

```powershell
# 方式 A：从 Ollama 社区直拉（最快）
ollama pull cliu/HuatuoGPT-o1-7B:latest
ollama cp cliu/HuatuoGPT-o1-7B:latest huatuo_o1_7b
ollama list   # 确认 huatuo_o1_7b:latest 出现
```

#### 6. 配置 .env

```powershell
# 复制模板
Copy-Item .env.example .env

# 用 notepad 打开
notepad .env
```

或者直接用 PowerShell 一行生成两个密钥写进去：

```powershell
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$authToken = ($bytes | %{ '{0:x2}' -f $_ }) -join ''

$bytes2 = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes2)
$piiKey = [Convert]::ToBase64String($bytes2).Replace('+','-').Replace('/','_')

@"
AUTH_TOKEN=$authToken
PII_ENCRYPTION_KEY=$piiKey
HOST=0.0.0.0
PORT=8000
EMBEDDING_ALLOW_DEGRADED=true
"@ | Set-Content -Path .env -Encoding UTF8
```

> 国内网络追加一行 `HF_ENDPOINT=https://hf-mirror.com` 加速首次 embedding 模型下载。

#### 7. 启动

```powershell
# 用项目内置脚本（推荐：会自动把 .env 注入到当前进程）
.\scripts\run.ps1

# 或者直接调（main.py 也会通过 python-dotenv 自动加载 .env）
uvicorn main:app --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000/ 即可。

#### Windows 常见坑速查

| 现象 | 原因 | 解决 |
|---|---|---|
| `Activate.ps1 不能被加载` | 执行策略 Restricted | `Set-ExecutionPolicy -Scope Process Bypass` |
| `pip install` 卡 numpy/torch | PyPI 太慢 | `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` |
| Tesseract 报 `chi_sim.traineddata 不存在` | 中文包没装 | 下 traineddata 丢进 `Tesseract-OCR\tessdata\` |
| OCR 总是失败 | 路径含中文/空格 | 把项目放到 `C:\code\` 等纯 ASCII 路径 |
| `~/.cache/torch/...` 路径过长 | Windows 260 字符限制 | 启用长路径：管理员 PowerShell `Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' LongPathsEnabled 1` |
| 端口 8000 不通 | Windows Defender 阻止 | 首次启动时弹窗点 "允许访问"；或预先开规则：`New-NetFirewallRule -DisplayName 'ZhihuYinban' -LocalPort 8000 -Protocol TCP -Direction Inbound -Action Allow` |
| 端口 8000 显示被占用但没进程 | Hyper-V 保留端口段 | 管理员：`net stop winnat` → `netsh int ipv4 add excludedportrange protocol=tcp startport=8000 numberofports=1` → `net start winnat` |
| 首次访问 8000 浏览器不弹窗就连不上 | Defender 安静拦截 | 同上手动加防火墙规则 |

</details>

---

## 🤖 本地大模型配置（HuatuoGPT-o1-7B）

项目默认用的是 **HuatuoGPT-o1-7B**（中文医疗大模型，约 8 GB），代码里用的名字叫 `huatuo_o1_7b`，
这个名字**只是本项目的本地别名**，Ollama 官方 Registry 上并没有这一条，直接 pull 会 404。
下面是三种获取方式，任选一种，**最终都要让 `ollama list` 能看到 `huatuo_o1_7b:latest`**。

### 模型出处（官方权重）

| 来源 | 链接 |
|---|---|
| 🤗 HuggingFace（原始权重） | [FreedomIntelligence/HuatuoGPT-o1-7B](https://huggingface.co/FreedomIntelligence/HuatuoGPT-o1-7B) |
| 🤗 HuggingFace（GGUF 量化） | [bartowski/HuatuoGPT-o1-7B-GGUF](https://huggingface.co/bartowski/HuatuoGPT-o1-7B-GGUF) |
| 📦 Ollama 社区（已打包） | [cliu/HuatuoGPT-o1-7B](https://ollama.com/cliu/HuatuoGPT-o1-7B) |
| 📄 GitHub（原项目 + 论文） | [FreedomIntelligence/HuatuoGPT-o1](https://github.com/FreedomIntelligence/HuatuoGPT-o1) |

### 方式 A：从 Ollama 社区直接拉（推荐，最省事）

```bash
# 1. 拉 8GB 左右，只需联网一次
ollama pull cliu/HuatuoGPT-o1-7B:latest

# 2. 给它起个本项目用的别名（代码里写死叫 huatuo_o1_7b）
ollama cp cliu/HuatuoGPT-o1-7B:latest huatuo_o1_7b

# 3. 验证
ollama list | grep huatuo_o1_7b
# 应看到：huatuo_o1_7b   latest   ...GB   <时间戳>
```

### 方式 B：从 HuggingFace 下 GGUF + Modelfile 导入（更可控）

适合断网环境 / 想自己挑量化精度（Q4_K_M 平衡版约 4.7 GB，Q8_0 高精度约 8.1 GB）：

```bash
# 1. 下载单个 GGUF 文件（任选一个量化）
#    下载页：https://huggingface.co/bartowski/HuatuoGPT-o1-7B-GGUF/tree/main
wget https://huggingface.co/bartowski/HuatuoGPT-o1-7B-GGUF/resolve/main/HuatuoGPT-o1-7B-Q4_K_M.gguf

# 2. 写一个 Modelfile（注意 FROM 的路径要对）
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

# 3. 导入并用本项目需要的名字注册
ollama create huatuo_o1_7b -f Modelfile

# 4. 验证
ollama list | grep huatuo_o1_7b
```

### 方式 C：用别的模型替代（硬件吃紧时）

项目并不绑死 HuatuoGPT，如果机器带不动 7B，可以换成任何 Ollama 支持的中文模型，
改一下环境变量里的模型名即可：

```bash
# 举例：用 4B 的 Qwen 2.5 顶上
ollama pull qwen2.5:3b

# 写进 .env（如果 .env 里已经有 OLLAMA_MODEL_NAME=... 这一行，
# 请直接编辑那一行，不要追加，否则会出现重复 key）
echo 'OLLAMA_MODEL_NAME=qwen2.5:3b' >> .env
```

> 注意：非医疗专用模型在"护理任务卡严格 JSON 输出"这个场景下，偶尔会吐出多余文字导致解析失败。
> 项目里有兜底重试，但医疗场景还是强烈建议用 HuatuoGPT-o1-7B。

### 启动 Ollama 服务

```bash
# Linux（systemd 安装方式已自动启）：
systemctl status ollama

# macOS / 手动模式：
ollama serve                      # 前台运行，日志直接可见
# 或后台：
nohup ollama serve > /tmp/ollama.log 2>&1 &
```

Ollama 默认监听 `http://localhost:11434`，本项目写死从这里拿模型（见
[`app/core/config.py`](./app/core/config.py) 的 `OLLAMA_API_URL`）。如果你把 Ollama 装在别的机器上，
要么在该机器上也启动本项目，要么改 `OLLAMA_API_URL` 指过去。

### 端到端验证（装完别急着接前端，先走一遍）

```bash
# 1. 直接调 Ollama，确认模型能回话
ollama run huatuo_o1_7b "你好，请用一句话介绍自己"

# 2. 用项目的 API 跑一次最小推理
curl -s http://localhost:11434/api/generate \
  -d '{"model":"huatuo_o1_7b","prompt":"高血压老人头晕该怎么处理？","stream":false}' \
  | head -c 300

# 3. 启动本项目后，健康检查应看到服务就绪
curl -s http://localhost:8000/health
```

### 常见坑

| 现象 | 原因 | 解决 |
|---|---|---|
| `pull model manifest: file does not exist` | 模型名拼错 / Registry 里没这条 | 走方式 A 的 `cliu/HuatuoGPT-o1-7B`，或方式 B 自己 create |
| `connection refused :11434` | Ollama 服务没起 | `ollama serve` 或 `systemctl start ollama` |
| 护理决策接口 503 "本地大模型不可用" | 项目启动时 Ollama 还没就绪 / 模型名对不上 | `ollama list` 确认 `huatuo_o1_7b:latest` 存在；重启本项目 |
| 第一次推理特别慢（10s+） | 冷启动要加载权重到显存/内存 | 正常现象，之后会快很多；想预热就先 `ollama run huatuo_o1_7b ""` |
| 内存炸 OOM | 16 GB 内存跑 Q8 偏紧 | 改用 Q4_K_M 量化（方式 B），或换方式 C 的小模型 |

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
# 注意：.env.example 里已经有空的 AUTH_TOKEN= 行，简单 echo >> 会出现重复 key。
# 用 sed 做"存在则替换、不存在则追加"的写法：
TOKEN=$(openssl rand -hex 32)
if grep -q '^AUTH_TOKEN=' .env 2>/dev/null; then
  sed -i.bak "s|^AUTH_TOKEN=.*|AUTH_TOKEN=$TOKEN|" .env && rm -f .env.bak
else
  echo "AUTH_TOKEN=$TOKEN" >> .env
fi

# 启动服务 → admin 用户自动创建，AUTH_TOKEN 成为其 API Key
# main.py 通过 python-dotenv 自动加载 .env，无需手动 source
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
├── docker-compose.yml              # 一键部署：app + ollama + 模型自动拉取
├── docker-compose.gpu.yml          # NVIDIA GPU overlay（可选）
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

### 方式 0：一键部署向导（最推荐）

什么都不用手动配 —— 脚本自动检测环境、生成密钥、选模型、拉起 Docker：

```bash
# Linux / macOS
git clone https://github.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban
chmod +x scripts/setup.sh
./scripts/setup.sh
```

```powershell
# Windows
git clone https://github.com/jiahuacaogoodman-art/Zhihu-Yinban.git
cd Zhihu-Yinban
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

向导会依次：
1. **(Windows)** 调用 `preflight.ps1` 检测 PowerShell / Docker / WSL2 backend / GPU / 端口占用 / Hyper-V 保留端口段 / 长路径支持 / 内存 / 防火墙
2. **(Linux/macOS)** 检测 Docker / Compose / NVIDIA GPU
3. 自动生成 AUTH_TOKEN + PII_ENCRYPTION_KEY（不依赖 openssl/python，PowerShell 用 .NET 原生 `RandomNumberGenerator`）
4. 让你选 LLM 后端：本地 Ollama 或远程 GPU API
5. 让你选模型量化档位（Q3/Q4/Q5/Q8/自定义）
6. 写入 `.env`（UTF-8 NoBOM + LF，避免容器内读取异常）
7. `docker compose up -d`
8. 等模型下载 + 等后端 healthy
9. **弹出访问地址 + 管理员 Token + 防火墙开放命令**

全程按回车就行，约 10 分钟搞定（首次下载模型取决于网速）。

---

### 方式 A：Docker Compose 手动部署（不想跑脚本时）

跟 setup.sh 做的事一样，但手动执行每一步：

```bash
# 1. 准备环境变量
cp .env.example .env

# 2. 在 .env 里填两个必填项
#    注意：.env.example 里 AUTH_TOKEN= 和 PII_ENCRYPTION_KEY= 已经存在（值为空），
#    直接 `echo ... >> .env` 会出现重复 key（多数解析器以最后一个为准，但容易让人误判）。
#    用下面的 sed/python 写法做"key 存在则替换、不存在才追加"：
AUTH_TOKEN=$(openssl rand -hex 32)
PII_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
python - <<EOF
from pathlib import Path, PurePath
import os, re
p = Path('.env'); txt = p.read_text() if p.exists() else ''
def upsert(t, k, v):
    pat = re.compile(rf'^{k}=.*$', re.M)
    return pat.sub(f'{k}={v}', t) if pat.search(t) else (t.rstrip()+f'\n{k}={v}\n')
txt = upsert(txt, 'AUTH_TOKEN',         os.environ['AUTH_TOKEN'])
txt = upsert(txt, 'PII_ENCRYPTION_KEY', os.environ['PII_KEY'])
p.write_text(txt)
EOF

# 3. 启动（首次会从 HuggingFace 下载 HuatuoGPT-o1-7B GGUF，约 4.8 GB）
#    ⚠️ 注意：ollama 和 model-puller 在 docker-compose.yml 里挂在 profiles: ["ollama"] 下，
#    不加 --profile ollama 这两个容器**不会启动**，model-puller 也就不会拉模型。
#    远程 LLM API（LLM_PROVIDER=openai）的部署不需要本地 ollama，可省略 --profile。
docker compose --profile ollama up -d

# 4. 看日志确认模型拉完
docker compose logs -f model-puller   # 看到 "[model-puller] done." 就是好了
docker compose logs -f app            # 后端启动日志

# 5. 浏览器访问 http://localhost:8000
```

**包含哪些服务？**

| 服务 | 作用 | 备注 |
|---|---|---|
| `ollama` | 本地大模型推理引擎 | 仅绑定 `127.0.0.1:11434`，不暴露公网 |
| `model-puller` | 一次性容器，自动 `ollama pull` 7B 模型 | 模型已存在则秒退 |
| `app` | 智护银伴 后端 + 前端 | 端口 `8000` |

**默认拉取的模型** = `hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M`（约 4.8 GB，CPU 也能跑）。
想换量化档位 / 换模型，改 `.env` 里 `OLLAMA_MODEL_NAME` 即可：

```env
# 极省内存（约 3.9 GB）
OLLAMA_MODEL_NAME=hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q3_K_M
# 推荐质量（约 5.5 GB，需 12 GB 内存）
OLLAMA_MODEL_NAME=hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q5_K_M
# 接近无损（约 8.2 GB，需 16 GB+ 内存）
OLLAMA_MODEL_NAME=hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q8_0
# 用通义千问做对比
OLLAMA_MODEL_NAME=qwen2.5:7b
```

**有 NVIDIA GPU？** 加一个 overlay：

```bash
docker compose --profile ollama -f docker-compose.yml -f docker-compose.gpu.yml up -d
docker exec yinban-ollama nvidia-smi   # 确认 GPU 可见
```

> 前提：宿主机已装 [NVIDIA Container Toolkit](https://github.com/NVIDIA/nvidia-container-toolkit)。

**常用运维命令：**

```bash
docker compose ps                      # 查看状态
docker compose logs -f app             # 跟踪后端日志
docker compose exec app sh             # 进容器调试
docker compose restart app             # 只重启业务，不重启 ollama
docker compose down                    # 停服务，但保留所有数据卷
docker compose down -v                 # ⚠️ 连数据一起删（含模型、病历、审计）
```

**数据卷一览**（`docker compose down` 不会删除）：

| 卷名 | 内容 | 必须备份？ |
|---|---|---|
| `ollama_models` | 大模型权重 | 否（可重新下载） |
| `ehr_db` | ChromaDB 向量库（病历） | ✅ **是** |
| `ehr_uploads` | 病历原图 + OCR | ✅ **是** |
| `auth_data` | 用户 + API Key | ✅ **是** |
| `audit_log` | 操作审计 | ✅ **是**（合规必备） |
| `nursing_events` | 护理事件流 | ✅ **是** |

备份示例：
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

### 方式 B：单容器手动跑（不推荐，仅供调试）

只适合"已经在外面跑了 Ollama"的场景：

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

> ⚠️ 必须挂 5 个卷，少一个就会丢数据。Compose 帮你处理好了，强烈推荐用方式 A。

---

### 方式 C：systemd（Linux 裸机部署）

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

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zhihuyinban
journalctl -u zhihuyinban -f
```

### 方式 D：Windows 计划任务（开机自启，systemd 的 Windows 等价物）

Windows 上不需要装第三方工具（NSSM / WinSW），直接用项目脚本：

```powershell
# 管理员 PowerShell
.\scripts\install-service.ps1

# 触发条件可选：
#   -Trigger AtLogon    （默认，用户登录时启动 - 推荐，能用到 Docker Desktop）
#   -Trigger AtStartup  （系统启动后 60 秒，需要 Docker Desktop 配置成 SYSTEM 启动）
```

它会创建一个 Windows 计划任务（Scheduled Task），开机/登录时自动 `docker compose up -d`，
日志写入 `service.log`。

```powershell
# 立即测试
Start-ScheduledTask -TaskName ZhihuYinban

# 查看状态
Get-ScheduledTask -TaskName ZhihuYinban | Get-ScheduledTaskInfo

# 查看日志
Get-Content service.log -Tail 50

# 卸载
.\scripts\uninstall-service.ps1
```

### 数据备份

#### Linux / macOS

**务必每日备份以下目录**（裸机部署）：
- `local_ehr_db/` — 全部向量数据
- `local_auth/` — 用户身份
- `local_audit_log/` — 审计留痕
- `local_ehr_uploads/` — 病历原图
- `local_nursing_events/` — 护理事件流

```bash
tar czf backup-$(date +%F).tgz \
  local_ehr_db/ local_auth/ local_audit_log/ \
  local_ehr_uploads/ local_nursing_events/
```

Docker 部署的备份命令见上方"方式 A"。

#### Windows

```powershell
# 一键备份所有 Docker 卷（输出 backups\yinban-backup-YYYY-MM-DD_HHMMSS.tgz）
.\scripts\backup.ps1

# 自定义输出目录
.\scripts\backup.ps1 -OutDir D:\Backups
```

#### 计划任务自动每日备份（Windows）

```powershell
# 管理员 PowerShell
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PWD\scripts\backup.ps1`" -OutDir D:\Backups"
$trigger = New-ScheduledTaskTrigger -Daily -At 3am
Register-ScheduledTask -TaskName 'ZhihuYinbanBackup' `
    -Action $action -Trigger $trigger -RunLevel Highest
```

每天凌晨 3 点自动备份到 `D:\Backups\`。建议把这个目录再同步到云盘 / NAS。

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
- [x] Docker Compose 一键部署 + HuggingFace 模型自动拉取
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
- 🏥 **民营养老机构**如有商业合作意向，可联系获取正式版：**jiahuacaogoodman@gmail.com**

Copyright (c) 2026 [jiahuaCao](https://github.com/jiahuacaogoodman-art)

---

<p align="center">
  如果这个项目帮到了你，请给个 ⭐ — 这是我继续写下去的最大动力。
</p>
