#Requires -Version 5.1
<#
.SYNOPSIS
    智护银伴 · Windows 国内网络一键部署
.DESCRIPTION
    对应 scripts/setup-cn.sh 的 Windows 实现：
      - 不依赖 VPN / 梯子
      - 自动配置 hf-mirror.com 加速 HuggingFace
      - 自动用清华 APT 镜像加速 Docker 构建
      - 提示配置 Docker Hub 镜像加速

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\setup-cn.ps1
#>

[CmdletBinding()]
param(
    [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir  = Split-Path -Parent $ScriptDir
$EnvFile     = Join-Path $ProjectDir '.env'
Set-Location $ProjectDir

function Write-Info    { param([string]$m) Write-Host "$([char]0x2139)  $m" -ForegroundColor Blue }
function Write-Success { param([string]$m) Write-Host "$([char]0x2714)  $m" -ForegroundColor Green }
function Write-Warn    { param([string]$m) Write-Host "$([char]0x26A0)  $m" -ForegroundColor Yellow }
function Write-Err     { param([string]$m) Write-Host "$([char]0x2718)  $m" -ForegroundColor Red }

Write-Host ''
Write-Host '  ╔══════════════════════════════════════════════════╗' -ForegroundColor Cyan
Write-Host '  ║   智护银伴 · 国内网络一键部署 (Windows)          ║' -ForegroundColor Cyan
Write-Host '  ║          无需梯子 · 全自动                       ║' -ForegroundColor Cyan
Write-Host '  ╚══════════════════════════════════════════════════╝' -ForegroundColor Cyan
Write-Host ''

# ── 检测 Docker ────────────────────────────────────────────
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Err 'Docker Desktop 未安装'
    Write-Host '  请先安装 Docker Desktop：'
    Write-Host '    winget install -e --id Docker.DockerDesktop'
    Write-Host '  或手动下载：https://www.docker.com/products/docker-desktop/'
    exit 1
}

try {
    docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw }
} catch {
    Write-Err 'Docker daemon 未运行，请打开 Docker Desktop 后重试'
    exit 1
}
Write-Success 'Docker 就绪'

try {
    docker compose version --short 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw }
} catch {
    Write-Err 'Docker Compose V2 不可用，请升级 Docker Desktop'
    exit 1
}
Write-Success 'Compose 就绪'

# ── 生成 .env ──────────────────────────────────────────────
function New-HexKey {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ($bytes | ForEach-Object { '{0:x2}' -f $_ }) -join ''
}
function New-FernetKey {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_')
}

if (-not (Test-Path $EnvFile)) {
    Write-Info '生成 .env 配置...'
    $token  = New-HexKey
    $piiKey = New-FernetKey

    $content = @"
# 智护银伴 · 国内网络配置（由 setup-cn.ps1 生成）
# 生成时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

AUTH_TOKEN=$token
PII_ENCRYPTION_KEY=$piiKey

HOST=0.0.0.0
PORT=8000
WORKERS=1
MAX_UPLOAD_SIZE_MB=15
EMBEDDING_ALLOW_DEGRADED=true
ANONYMIZED_TELEMETRY=False

# HuggingFace 国内镜像，加速 embedding 模型下载
HF_ENDPOINT=https://hf-mirror.com

# --- LLM Provider（默认 ollama） ---
LLM_PROVIDER=ollama
OLLAMA_MODEL_NAME=hf.co/mradermacher/HuatuoGPT-o1-7B-GGUF:Q4_K_M

# 切到远程 API 时取消下面注释并填值：
# LLM_PROVIDER=openai
# OPENAI_API_BASE=https://api.deepseek.com/v1
# OPENAI_MODEL=deepseek-chat
# OPENAI_API_KEY=sk-xxx
"@
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($EnvFile, ($content -replace "`r`n", "`n") + "`n", $utf8NoBom)
    Write-Success '已生成 .env (含 HF 国内镜像)'
} else {
    # 已有 .env，确保有 HF_ENDPOINT
    $envText = Get-Content -Path $EnvFile -Encoding UTF8 -Raw
    if ($envText -notmatch 'HF_ENDPOINT') {
        Add-Content -Path $EnvFile -Value 'HF_ENDPOINT=https://hf-mirror.com' -Encoding UTF8
        Write-Info '已追加 HF_ENDPOINT=https://hf-mirror.com 到 .env'
    }
    Write-Success '使用已有 .env'
}

# ── Docker Hub 镜像加速提示 ────────────────────────────────
Write-Host ''
Write-Info '提示：如果 docker pull 慢，可在 Docker Desktop 配置镜像加速'
Write-Host '  Docker Desktop → Settings → Docker Engine → 加入：' -ForegroundColor White
Write-Host '    "registry-mirrors": ["https://docker.mirrors.ustc.edu.cn", "https://hub-mirror.c.163.com"]' -ForegroundColor Cyan
Write-Host '  保存后点 "Apply & Restart"，重跑本脚本'
Write-Host ''

# ── 检查端口 ──────────────────────────────────────────────
function Test-PortFree {
    param([int]$Port)
    try {
        $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $l.Start(); $l.Stop(); return $true
    } catch { return $false }
}

if (-not (Test-PortFree 8000)) {
    Write-Warn '端口 8000 已被占用，可能与本系统冲突'
    Write-Host '  查看占用进程：'
    Write-Host "    Get-NetTCPConnection -LocalPort 8000 | Select-Object -ExpandProperty OwningProcess | %{ Get-Process -Id `$_ }"
    Write-Host ''
}

# ── 构建（清华 APT 镜像）──────────────────────────────────
Write-Info '开始构建镜像（使用清华 APT 镜像加速）...'
Write-Host ''

& docker compose build --build-arg APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn
if ($LASTEXITCODE -ne 0) {
    Write-Err '镜像构建失败'
    Write-Host '  常见原因：'
    Write-Host '    1. Docker Hub 拉取 python:3.11-slim 失败 → 配置上方提到的镜像加速'
    Write-Host '    2. APT 仓库不通 → 检查代理设置（Docker Desktop → Settings → Resources → Proxies）'
    exit 1
}

Write-Host ''
Write-Success '镜像构建完成'

# ── 启动 ──────────────────────────────────────────────────
$llmProvider = 'ollama'
$envText = Get-Content -Path $EnvFile -Encoding UTF8 -Raw
if ($envText -match '(?m)^LLM_PROVIDER=(\S+)') {
    $llmProvider = $Matches[1]
}

$composeArgs = @()
if ($llmProvider -ne 'openai') {
    $composeArgs = @('--profile', 'ollama')
    Write-Info '使用本地 Ollama（首次需下载模型）'
} else {
    Write-Info '检测到 LLM_PROVIDER=openai，跳过 Ollama 容器'
}
$composeArgs += @('up', '-d')

Write-Info '启动服务...'
& docker compose @composeArgs
if ($LASTEXITCODE -ne 0) {
    Write-Err 'docker compose up 失败'
    exit 1
}
Write-Success '容器已启动'

# ── 等模型下载 ────────────────────────────────────────────
if ($llmProvider -ne 'openai') {
    Write-Host ''
    Write-Info '等待 Ollama 模型下载（首次约 5-30 分钟）...'
    Write-Host '  另开 PowerShell 看进度: docker compose logs -f model-puller'

    $maxWait = 1800; $waited = 0
    while ($waited -lt $maxWait) {
        try {
            $status = docker inspect --format='{{.State.Status}}' yinban-model-puller 2>$null
        } catch { $status = 'unknown' }
        if ($status -eq 'exited') {
            $code = docker inspect --format='{{.State.ExitCode}}' yinban-model-puller 2>$null
            if ($code -eq '0') {
                Write-Host ''; Write-Success '模型下载完成'; break
            } else {
                Write-Host ''; Write-Warn "模型下载失败，查看: docker compose logs model-puller"
                break
            }
        }
        Start-Sleep -Seconds 10; $waited += 10
        Write-Host -NoNewline "`r  $([char]0x23F3) 已等待 $waited s..."
    }
    Write-Host ''
}

# ── 等后端 ───────────────────────────────────────────────
Write-Info '等待后端启动...'
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        Invoke-WebRequest -Uri 'http://localhost:8000/health' -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop | Out-Null
        $ready = $true; break
    } catch {
        Start-Sleep -Seconds 4
    }
}
if ($ready) {
    Write-Success '后端就绪'
} else {
    Write-Warn '后端启动超时，请运行 docker compose logs app 查看日志'
}

# ── 完成 ─────────────────────────────────────────────────
$token = (Get-Content -Path $EnvFile -Encoding UTF8 |
    Where-Object { $_ -match '^AUTH_TOKEN=(.+)$' } |
    Select-Object -First 1) -replace '^AUTH_TOKEN=', ''

Write-Host ''
Write-Host '  ╔══════════════════════════════════════════════════╗' -ForegroundColor Green
Write-Host '  ║              部署成功！                          ║' -ForegroundColor Green
Write-Host '  ╚══════════════════════════════════════════════════╝' -ForegroundColor Green
Write-Host ''
Write-Host '  管理端：http://localhost:8000/' -ForegroundColor Cyan
Write-Host '  护工端：http://localhost:8000/nurse' -ForegroundColor Cyan
Write-Host ''
Write-Host '  管理员 Token：' -ForegroundColor White
Write-Host "    $token" -ForegroundColor Yellow
Write-Host ''
Write-Host '  查看完整配置: notepad .env' -ForegroundColor Cyan
Write-Host '  查看日志:     docker compose logs -f app' -ForegroundColor Cyan
Write-Host '  停止服务:     docker compose down' -ForegroundColor Cyan
Write-Host '  开机自启:     .\scripts\install-service.ps1' -ForegroundColor Cyan
Write-Host ''
