#Requires -Version 5.1
<#
.SYNOPSIS
    智护银伴 · Windows 一键部署向导（PowerShell 版）
    ZhiHu YinBan One-Click Deployment Wizard for Windows

.DESCRIPTION
    对应 scripts/setup.sh 的完整 Windows 实现。
    无需 Python / openssl / curl / WSL —— 仅用 PowerShell 5.1+ 原生 API。

    流程：
      0. 调用 preflight.ps1 做环境诊断
      1. 检测 Docker / Compose / Daemon
      2. 自动生成 AUTH_TOKEN + PII_ENCRYPTION_KEY（System.Security.Cryptography）
      3. 选 LLM 后端（本地 Ollama / 远程 OpenAI 兼容）
      4. 选模型量化档位
      5. 探测 NVIDIA GPU + WSL2 直通
      6. 写入 .env (UTF-8 无 BOM, LF 行尾，避免容器内读取异常)
      7. docker compose up -d
      8. 等模型下载 + 等后端 healthy
      9. 输出访问地址 + Token + 防火墙提醒

.PARAMETER NonInteractive
    跳过所有交互，使用默认值（CI / 自动化场景）
.PARAMETER SkipPreflight
    跳过 preflight.ps1 检查（不推荐）
.PARAMETER Reset
    丢弃现有 .env，重新生成

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1

.EXAMPLE
    # 完全无人值守
    .\scripts\setup.ps1 -NonInteractive
#>

[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$SkipPreflight,
    [switch]$Reset
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

# ── 路径解析 ─────────────────────────────────────────────────
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir  = Split-Path -Parent $ScriptDir
$EnvFile     = Join-Path $ProjectDir '.env'
$EnvExample  = Join-Path $ProjectDir '.env.example'
Set-Location $ProjectDir

# ── 输出工具 ─────────────────────────────────────────────────
function Write-Info    { param([string]$msg) Write-Host "$([char]0x2139)  $msg" -ForegroundColor Blue }
function Write-Success { param([string]$msg) Write-Host "$([char]0x2714)  $msg" -ForegroundColor Green }
function Write-Warn    { param([string]$msg) Write-Host "$([char]0x26A0)  $msg" -ForegroundColor Yellow }
function Write-Err     { param([string]$msg) Write-Host "$([char]0x2718)  $msg" -ForegroundColor Red }
function Write-Header {
    param([string]$msg)
    Write-Host ''
    Write-Host "$([char]0x2501)$([char]0x2501)$([char]0x2501) $msg $([char]0x2501)$([char]0x2501)$([char]0x2501)" -ForegroundColor Cyan
    Write-Host ''
}
function Read-Choice {
    <#
    交互输入。NonInteractive 模式直接用默认值，绝不阻塞 CI。
    #>
    param(
        [string]$Prompt,
        [string]$Default = ''
    )
    if ($NonInteractive) {
        Write-Host "$Prompt $Default (non-interactive)" -ForegroundColor DarkGray
        return $Default
    }
    Write-Host $Prompt -NoNewline -ForegroundColor White
    $reply = Read-Host
    if ([string]::IsNullOrWhiteSpace($reply)) { return $Default }
    return $reply.Trim()
}
function Read-YesNo {
    param([string]$Prompt, [bool]$Default = $true)
    $hint = if ($Default) { '[Y/n]' } else { '[y/N]' }
    $reply = Read-Choice "$Prompt $hint`: "
    if ([string]::IsNullOrWhiteSpace($reply)) { return $Default }
    return $reply -match '^[yY]'
}
function Format-MaskedSecret {
    param([string]$Secret)
    if ($Secret.Length -le 8) { return '****' }
    return ('{0}****{1}' -f $Secret.Substring(0,4), $Secret.Substring($Secret.Length - 4))
}

# ── Banner ──────────────────────────────────────────────────
Write-Host ''
Write-Host '  ╔══════════════════════════════════════════════════╗' -ForegroundColor Cyan
Write-Host '  ║       智护银伴 · Windows 一键部署向导            ║' -ForegroundColor Cyan
Write-Host '  ║   ZhiHu YinBan Deployment Wizard (Windows)       ║' -ForegroundColor Cyan
Write-Host '  ╚══════════════════════════════════════════════════╝' -ForegroundColor Cyan
Write-Host ''

# ── Step 0: Preflight ───────────────────────────────────────
if (-not $SkipPreflight) {
    Write-Header 'Step 0/7 · 环境诊断'
    $preflight = Join-Path $ScriptDir 'preflight.ps1'
    if (Test-Path $preflight) {
        # Quick mode 跳过 GPU 容器测试（耗时）
        & $preflight -Quick
        if ($LASTEXITCODE -ne 0) {
            Write-Err '环境诊断未通过，请按提示修复后重跑。'
            Write-Host ''
            Write-Host '  如果你确定已修复，可加 -SkipPreflight 跳过：'
            Write-Host '    .\scripts\setup.ps1 -SkipPreflight'
            exit 1
        }
    }
}

# ── Step 1: Docker 自动安装 ─────────────────────────────────
Write-Header 'Step 1/7 · 检测 Docker'

function Test-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    try {
        docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

if (-not (Test-DockerReady)) {
    Write-Err 'Docker Desktop 未就绪'
    Write-Host ''
    Write-Host '  Windows 上有三种安装方式：' -ForegroundColor White
    Write-Host ''
    Write-Host '    [1] winget 自动安装（推荐，需要 Win10 1809+）' -ForegroundColor White
    Write-Host '        winget install -e --id Docker.DockerDesktop' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '    [2] 官网下载安装器' -ForegroundColor White
    Write-Host '        https://www.docker.com/products/docker-desktop/' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '    [3] Chocolatey' -ForegroundColor White
    Write-Host '        choco install docker-desktop' -ForegroundColor Cyan
    Write-Host ''

    if (-not $NonInteractive -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        if (Read-YesNo '  是否现在用 winget 自动安装 Docker Desktop?' $false) {
            Write-Info '正在通过 winget 安装 Docker Desktop（约 500 MB，需要重启）...'
            try {
                winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
                Write-Host ''
                Write-Warn 'Docker Desktop 已安装。请：'
                Write-Host '   1. 重启电脑（首次安装必须）' -ForegroundColor Yellow
                Write-Host '   2. 启动后从开始菜单打开 Docker Desktop' -ForegroundColor Yellow
                Write-Host '   3. 等待右下角图标变成稳定的鲸鱼' -ForegroundColor Yellow
                Write-Host '   4. 重新运行本脚本' -ForegroundColor Yellow
                exit 0
            } catch {
                Write-Err "winget 安装失败：$_"
                Write-Host '  请改用手动下载安装器：https://www.docker.com/products/docker-desktop/'
                exit 1
            }
        }
    }
    exit 1
}

$dockerVer = (docker --version) -join ''
Write-Success "Docker 已就绪：$dockerVer"

# Compose V2
$composeOk = $false
try {
    $composeVer = (docker compose version --short 2>$null) -join ''
    if ($LASTEXITCODE -eq 0 -and $composeVer) {
        Write-Success "Docker Compose V2: v$composeVer"
        $composeOk = $true
    }
} catch {}

if (-not $composeOk) {
    Write-Err 'Docker Compose V2 不可用（你的 Docker Desktop 版本太旧）'
    Write-Host '  请升级到 Docker Desktop 4.x+：'
    Write-Host '    winget upgrade Docker.DockerDesktop'
    exit 1
}

# Daemon
$dockerInfo = $null
try {
    $dockerInfo = docker info --format '{{json .}}' 2>$null | ConvertFrom-Json
} catch {}
if (-not $dockerInfo) {
    Write-Err 'Docker daemon 未运行'
    Write-Host '  请打开 Docker Desktop（开始菜单 → Docker Desktop）'
    Write-Host '  等待右下角鲸鱼图标显示 "Docker Desktop is running" 后重跑本脚本'
    exit 1
}
Write-Success "Docker daemon 运行中：$($dockerInfo.OperatingSystem)"

# ── Step 2: 密钥 ────────────────────────────────────────────
Write-Header 'Step 2/7 · 配置安全密钥'

# 用 .NET 原生 RandomNumberGenerator 生成 - 不依赖 openssl/python
function New-HexKey {
    param([int]$ByteLength = 32)
    $bytes = New-Object byte[] $ByteLength
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ($bytes | ForEach-Object { '{0:x2}' -f $_ }) -join ''
}

function New-FernetKey {
    <#
    生成 Fernet 兼容的 32 字节 URL-safe base64 编码（44 字符含 '=' padding）
    Python cryptography 库的 Fernet.generate_key() 等价实现
    #>
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    # URL-safe base64: 把 + → -, / → _
    $b64 = [Convert]::ToBase64String($bytes)
    $b64 = $b64.Replace('+', '-').Replace('/', '_')
    return $b64
}

# 解析现有 .env 中的某个 key
function Get-EnvVar {
    param([string]$File, [string]$Name)
    if (-not (Test-Path $File)) { return $null }
    $line = Get-Content -Path $File -Encoding UTF8 -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^${Name}=(.*)$" } |
        Select-Object -First 1
    if ($line -match "^${Name}=(.*)$") { return $Matches[1].Trim() }
    return $null
}

# AUTH_TOKEN
Write-Host '  管理员 Token (AUTH_TOKEN)' -ForegroundColor White
Write-Host '  这是部署后的管理员登录凭证，相当于超级密码。'
Write-Host ''

$existingToken = if ($Reset) { $null } else { Get-EnvVar -File $EnvFile -Name 'AUTH_TOKEN' }
$AuthToken = $null
if ($existingToken) {
    Write-Host "  已有 Token: $(Format-MaskedSecret $existingToken)" -ForegroundColor Yellow
    if (Read-YesNo '  是否保留现有 Token?' $true) {
        $AuthToken = $existingToken
        Write-Success '保留现有 AUTH_TOKEN'
    } else {
        $AuthToken = New-HexKey 32
        Write-Success '已生成新 AUTH_TOKEN'
        Write-Host "    $AuthToken" -ForegroundColor Yellow
    }
} else {
    if (-not $NonInteractive -and -not (Read-YesNo '  自动生成随机 Token?' $true)) {
        $AuthToken = Read-Choice '  请粘贴你的 AUTH_TOKEN: '
        if ([string]::IsNullOrWhiteSpace($AuthToken)) {
            Write-Err 'Token 不能为空'
            exit 1
        }
    } else {
        $AuthToken = New-HexKey 32
        Write-Success '已自动生成 AUTH_TOKEN'
        Write-Host "    $AuthToken" -ForegroundColor Yellow
        Write-Host ''
        Write-Host '    ⚠ 请立即复制保存，部署完成后也会再次显示。' -ForegroundColor Red
    }
}
Write-Host ''

# PII_ENCRYPTION_KEY
Write-Host '  PII 加密密钥 (PII_ENCRYPTION_KEY)' -ForegroundColor White
Write-Host '  用于加密病历中的敏感信息（姓名、身份证、联系方式等）。'
Write-Host '  ⚠ 此密钥一旦丢失，已加密的数据将无法解密。请务必备份 .env 文件。' -ForegroundColor Red
Write-Host ''

$existingPii = if ($Reset) { $null } else { Get-EnvVar -File $EnvFile -Name 'PII_ENCRYPTION_KEY' }
$PiiKey = $null
if ($existingPii) {
    Write-Host "  已有密钥: $(Format-MaskedSecret $existingPii)" -ForegroundColor Yellow
    if (Read-YesNo '  是否保留现有密钥?' $true) {
        $PiiKey = $existingPii
        Write-Success '保留现有 PII_ENCRYPTION_KEY'
    } else {
        $PiiKey = New-FernetKey
        Write-Success '已生成新 PII_ENCRYPTION_KEY'
        Write-Host "    $PiiKey" -ForegroundColor Yellow
    }
} else {
    $PiiKey = New-FernetKey
    Write-Success '已自动生成 PII_ENCRYPTION_KEY'
    Write-Host "    $PiiKey" -ForegroundColor Yellow
}
Write-Host ''

# ── Step 3: LLM Provider ────────────────────────────────────
Write-Header 'Step 3/7 · LLM 推理后端选择'

Write-Host '  项目支持两种 LLM 推理方式：' -ForegroundColor White
Write-Host ''
Write-Host '    [1] 本地 Ollama（默认，推荐）' -ForegroundColor White
Write-Host '        Docker Compose 自动启动 Ollama + 自动下载模型'
Write-Host '        适合：单机部署 / 没有 GPU 服务器 / 离线优先'
Write-Host ''
Write-Host '    [2] 远程 GPU / OpenAI 兼容 API' -ForegroundColor White
Write-Host '        指向机房 vLLM / TGI / DeepSeek / 智谱等'
Write-Host '        适合：有专门的 GPU 卡跑推理'
Write-Host ''

$llmChoice = Read-Choice '  选择 [1/2] (默认 1): ' '1'

$LlmProvider = 'ollama'
$OpenAIApiBase = ''
$OpenAIModel = ''
$OpenAIApiKey = ''

if ($llmChoice -eq '2') {
    $LlmProvider = 'openai'
    Write-Host ''
    Write-Host '  OpenAI 兼容端点配置' -ForegroundColor White
    Write-Host '  示例：'
    Write-Host '    vLLM:     http://192.168.1.100:8000/v1'
    Write-Host '    DeepSeek: https://api.deepseek.com/v1'
    Write-Host ''

    $OpenAIApiBase = Read-Choice '  OPENAI_API_BASE (必填): '
    if ([string]::IsNullOrWhiteSpace($OpenAIApiBase)) {
        Write-Err 'OPENAI_API_BASE 不能为空'
        exit 1
    }
    $OpenAIModel = Read-Choice '  OPENAI_MODEL (必填，如 Qwen/Qwen2.5-7B-Instruct): '
    if ([string]::IsNullOrWhiteSpace($OpenAIModel)) {
        Write-Err 'OPENAI_MODEL 不能为空'
        exit 1
    }
    $OpenAIApiKey = Read-Choice '  OPENAI_API_KEY (选填，自建服务留空): '
    Write-Success "已配置远程 LLM: $OpenAIApiBase → $OpenAIModel"
}

# ── Step 4: Ollama 模型 + GPU ───────────────────────────────
$OllamaModelName = 'hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M'
$UseGpu = $false

if ($LlmProvider -eq 'ollama') {
    Write-Header 'Step 4/7 · Ollama 模型配置'

    # 检测本地是否已有 Ollama 在跑
    try {
        $resp = Invoke-WebRequest -Uri 'http://localhost:11434/' -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            Write-Success '检测到本地 Ollama 服务已运行 (localhost:11434)'
            Write-Warn 'Docker Compose 仍会启动容器化 Ollama（端口冲突时启动会失败）'
            Write-Host '  建议：要么停掉本机 Ollama，要么编辑 .env 设置 OLLAMA_API_URL=http://host.docker.internal:11434/api/generate'
        }
    } catch {
        Write-Info '未检测到本地 Ollama（将使用容器化 Ollama）'
    }
    Write-Host ''

    Write-Host '  选择模型量化档位：' -ForegroundColor White
    Write-Host ''
    Write-Host '    [1] Q3_K_M  ~3.9 GB  (极省内存，8GB 内存可跑，质量略降)'
    Write-Host '    [2] Q4_K_M  ~4.8 GB  (默认推荐，16GB 内存流畅)' -ForegroundColor Green
    Write-Host '    [3] Q5_K_M  ~5.5 GB  (推荐质量，需 12GB+ 内存)'
    Write-Host '    [4] Q8_0    ~8.2 GB  (接近无损，需 16GB+ 内存)'
    Write-Host '    [5] 自定义模型名（如 qwen2.5:7b 或其它 HF GGUF）'
    Write-Host ''

    $modelChoice = Read-Choice '  选择 [1-5] (默认 2): ' '2'

    switch ($modelChoice) {
        '1' { $OllamaModelName = 'hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q3_K_M'; Write-Success '已选择 Q3_K_M' }
        '2' { $OllamaModelName = 'hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M'; Write-Success '已选择 Q4_K_M (推荐)' }
        '3' { $OllamaModelName = 'hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q5_K_M'; Write-Success '已选择 Q5_K_M' }
        '4' { $OllamaModelName = 'hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q8_0';   Write-Success '已选择 Q8_0' }
        '5' {
            $custom = Read-Choice '  请输入完整模型名: '
            if ([string]::IsNullOrWhiteSpace($custom)) {
                Write-Warn '输入为空，使用默认 Q4_K_M'
            } else {
                $OllamaModelName = $custom
                Write-Success "已设置自定义模型: $OllamaModelName"
            }
        }
        default { Write-Success '使用默认 Q4_K_M' }
    }

    # GPU 检测（仅 Windows + WSL2 backend 有意义）
    Write-Host ''
    $hasGpu = $false
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        try {
            $gpuLine = (nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null | Select-Object -First 1)
            if ($gpuLine) {
                Write-Success '检测到 NVIDIA GPU:'
                Write-Host "        $gpuLine" -ForegroundColor DarkGray
                $hasGpu = $true
            }
        } catch {}
    }

    if ($hasGpu) {
        # WSL2 backend 才能用 GPU
        $isWsl = $dockerInfo.OperatingSystem -match 'WSL' -or $dockerInfo.KernelVersion -match 'WSL'
        if (-not $isWsl) {
            Write-Warn 'Docker Desktop 未使用 WSL2 backend，无法用 GPU'
            Write-Host '  Docker Desktop → Settings → General → Use WSL 2 based engine'
            $hasGpu = $false
        } else {
            if (Read-YesNo '  启用 GPU 加速?' $true) {
                $UseGpu = $true
                Write-Success '将启用 GPU 加速 (docker-compose.gpu.yml)'
            }
        }
    } else {
        Write-Info '未检测到 NVIDIA GPU（将使用 CPU 推理，速度稍慢但完全可用）'
    }
}

# ── Step 5: 写 .env ─────────────────────────────────────────
Write-Header 'Step 5/7 · 生成配置文件'

# 关键：必须 UTF-8 NoBOM + LF 行尾
# Docker Compose 在 Windows 上读 UTF-16 BOM 会把 BOM 当成 KEY 的一部分 → AUTH_TOKEN 失效
$envContent = @"
# ============================================================
# 智护银伴 · 部署配置（由 setup.ps1 自动生成）
# 生成时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
# ============================================================

# --- 鉴权 ---
AUTH_TOKEN=$AuthToken

# --- PII 加密 ---
PII_ENCRYPTION_KEY=$PiiKey

# --- 服务监听 ---
HOST=0.0.0.0
PORT=8000

# --- LLM Provider ---
LLM_PROVIDER=$LlmProvider

# --- Ollama 配置 ---
OLLAMA_MODEL_NAME=$OllamaModelName
# OLLAMA_API_URL=http://localhost:11434/api/generate

# --- OpenAI 兼容配置（LLM_PROVIDER=openai 时生效）---
OPENAI_API_BASE=$OpenAIApiBase
OPENAI_MODEL=$OpenAIModel
OPENAI_API_KEY=$OpenAIApiKey

# --- 其它 ---
MAX_UPLOAD_SIZE_MB=15
WORKERS=1
RELOAD=0
EMBEDDING_ALLOW_DEGRADED=true
ANONYMIZED_TELEMETRY=False
"@

# 确保 LF + UTF-8 NoBOM
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($EnvFile, ($envContent -replace "`r`n", "`n") + "`n", $utf8NoBom)

Write-Success "配置已写入: $EnvFile"
Write-Host ''
Write-Host '  生成的关键配置：'
Write-Host "    AUTH_TOKEN         = $(Format-MaskedSecret $AuthToken)" -ForegroundColor Yellow
Write-Host "    PII_ENCRYPTION_KEY = $(Format-MaskedSecret $PiiKey)" -ForegroundColor Yellow
Write-Host "    LLM Provider       = $LlmProvider" -ForegroundColor Cyan
if ($LlmProvider -eq 'ollama') {
    Write-Host "    模型               = $OllamaModelName" -ForegroundColor Cyan
} else {
    Write-Host "    API Base           = $OpenAIApiBase" -ForegroundColor Cyan
    Write-Host "    模型               = $OpenAIModel" -ForegroundColor Cyan
}

# ── Step 6: 启动 Compose ────────────────────────────────────
Write-Header 'Step 6/7 · 启动服务'

if (-not (Read-YesNo '  确认启动部署?' $true)) {
    Write-Info '已取消。配置文件已保存，稍后可手动运行：'
    Write-Host '    docker compose up -d' -ForegroundColor Cyan
    exit 0
}

# 构建 compose 参数
$composeFiles = @('-f', 'docker-compose.yml')
if ($UseGpu) {
    $composeFiles += @('-f', 'docker-compose.gpu.yml')
    Write-Info '启用 GPU overlay'
}

$composeProfiles = @()
if ($LlmProvider -eq 'ollama') {
    $composeProfiles = @('--profile', 'ollama')
    Write-Info '启用本地 Ollama（首次需下载模型，约 5-30 分钟）'
} else {
    Write-Info '使用远程 API，跳过 Ollama 容器'
}

# 网络优化询问
Write-Host ''
Write-Host '  构建选项（网络优化）：' -ForegroundColor White
Write-Host '    [1] 默认（直接构建，海外/科学上网）'
Write-Host '    [2] 国内加速（清华 APT 镜像）'
Write-Host '    [3] 离线优先（仅用本地缓存）'
Write-Host ''
$netChoice = Read-Choice '  选择 [1-3] (默认 1): ' '1'

$buildExtra = @()
switch ($netChoice) {
    '2' {
        $buildExtra = @('--build-arg', 'APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn')
        Write-Info '使用清华 APT 镜像加速'
    }
    '3' {
        $buildExtra = @('--no-pull')
        Write-Info '离线模式：跳过基础镜像拉取'
    }
    default { Write-Info '使用默认网络设置' }
}

Write-Info '正在启动服务...'
Write-Host ''

# 拼装最终命令
$composeArgs = $composeFiles + $composeProfiles + @('up', '-d', '--build') + $buildExtra
Write-Host "  > docker compose $($composeArgs -join ' ')" -ForegroundColor DarkGray
Write-Host ''

& docker compose @composeArgs
if ($LASTEXITCODE -ne 0) {
    Write-Err 'docker compose up 失败'
    Write-Host ''
    Write-Host '  常见原因：'
    Write-Host '    1. 端口冲突：检查 8000/11434 是否被占用 → .\scripts\preflight.ps1'
    Write-Host '    2. 镜像拉取失败：网络问题 → 选 [2] 国内镜像 重试'
    Write-Host '    3. 磁盘不足：清理 Docker 镜像 → docker system prune -a'
    exit 1
}

Write-Success '容器已启动'

# ── 等模型下载 ──────────────────────────────────────────────
if ($LlmProvider -eq 'ollama') {
    Write-Host ''
    Write-Info '正在等待模型下载完成（首次约 5-30 分钟，取决于网速）...'
    Write-Host '  另开 PowerShell 看进度: docker compose logs -f model-puller'

    $maxWait = 1800
    $waited = 0
    $interval = 10
    while ($waited -lt $maxWait) {
        try {
            $status = docker inspect --format='{{.State.Status}}' yinban-model-puller 2>$null
        } catch { $status = 'unknown' }

        if ($status -eq 'exited') {
            $exitCode = docker inspect --format='{{.State.ExitCode}}' yinban-model-puller 2>$null
            if ($exitCode -eq '0') {
                Write-Host ''
                Write-Success '模型下载完成！'
                break
            } else {
                Write-Host ''
                Write-Err "模型下载失败 (exit code: $exitCode)"
                Write-Host '  查看日志：docker compose logs model-puller'
                exit 1
            }
        }
        Write-Host -NoNewline "`r  $([char]0x23F3) 已等待 $waited s / 模型下载中..."
        Start-Sleep -Seconds $interval
        $waited += $interval
    }
    if ($waited -ge $maxWait) {
        Write-Host ''
        Write-Warn '等待超时（30分钟），模型可能仍在下载中'
        Write-Host '  请手动检查：docker compose logs -f model-puller'
    }
}

# ── 等后端 healthy ──────────────────────────────────────────
Write-Host ''
Write-Info '等待后端启动...'

$maxWait = 120
$waited = 0
$ready = $false
while ($waited -lt $maxWait) {
    try {
        $resp = Invoke-WebRequest -Uri 'http://localhost:8000/health' -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -eq 200) {
            Write-Host ''
            Write-Success '后端服务已就绪！'
            $ready = $true
            break
        }
    } catch {
        Write-Host -NoNewline "`r  $([char]0x23F3) 等待后端启动 $waited s..."
    }
    Start-Sleep -Seconds 5
    $waited += 5
}

if (-not $ready) {
    Write-Host ''
    Write-Warn '后端启动超时，请检查日志：'
    Write-Host '  docker compose logs app'
    exit 1
}

# ── Step 7: 完成 ────────────────────────────────────────────
Write-Header 'Step 7/7 · 部署完成'

Write-Host ''
Write-Host '  ╔══════════════════════════════════════════════════╗' -ForegroundColor Green
Write-Host '  ║              部署成功！                          ║' -ForegroundColor Green
Write-Host '  ╚══════════════════════════════════════════════════╝' -ForegroundColor Green
Write-Host ''
Write-Host '  访问地址：' -ForegroundColor White
Write-Host '    管理端：  http://localhost:8000/' -ForegroundColor Cyan
Write-Host '    护工端：  http://localhost:8000/nurse' -ForegroundColor Cyan
Write-Host '    健康检查：http://localhost:8000/health' -ForegroundColor Cyan
Write-Host ''
Write-Host '  管理员 Token（请妥善保管）：' -ForegroundColor White
Write-Host "    $AuthToken" -ForegroundColor Yellow
Write-Host ''
Write-Host '  PII 加密密钥（备份用，丢失将无法解密已有数据）：' -ForegroundColor White
Write-Host "    $PiiKey" -ForegroundColor Yellow
Write-Host ''

# 防火墙提醒
Write-Host '  防火墙提醒：' -ForegroundColor White
Write-Host '    首次访问 8000 端口时 Windows Defender 可能弹窗要求授权'
Write-Host '    点 "允许访问" 即可。'
Write-Host ''
Write-Host '    若想开放给局域网访问（其他电脑用 http://本机IP:8000）：'
Write-Host '      管理员 PowerShell 执行：'
Write-Host '      New-NetFirewallRule -DisplayName ''ZhihuYinban'' -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow' -ForegroundColor Cyan
Write-Host ''

Write-Host '  常用命令：' -ForegroundColor White
Write-Host '    查看状态：  docker compose ps' -ForegroundColor Cyan
Write-Host '    查看日志：  docker compose logs -f app' -ForegroundColor Cyan
Write-Host '    停止服务：  docker compose down' -ForegroundColor Cyan
Write-Host '    重启应用：  docker compose restart app' -ForegroundColor Cyan
Write-Host '    数据备份：  .\scripts\backup.ps1' -ForegroundColor Cyan
Write-Host '    开机自启：  .\scripts\install-service.ps1' -ForegroundColor Cyan
Write-Host ''
Write-Host '  完整密钥保存在 .env 文件中：' -ForegroundColor White
Write-Host "    notepad $EnvFile" -ForegroundColor Cyan
Write-Host ''
Write-Host '  ────────────────────────────────────────────────'
Write-Host '  如果这个项目帮到了你，请给个 Star'
Write-Host '  https://github.com/jiahuacaogoodman-art/Zhihu-Yinban' -ForegroundColor Cyan
Write-Host ''
