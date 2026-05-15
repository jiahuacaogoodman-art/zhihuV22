#Requires -Version 5.1
<#
.SYNOPSIS
    智护银伴 · Windows 本地应用化启动器
.DESCRIPTION
    面向试点/演示/院内部署的「一键启动」入口：
      - 自动定位项目根目录
      - 自动创建/复用 venv
      - 自动安装 Python 依赖
      - 自动初始化 .env
      - 自动检测端口占用
      - 自动启动 FastAPI 后端
      - 自动等待 /health 就绪并打开浏览器
      - 可选跳过依赖安装、跳过浏览器、允许无 Ollama 启动

    设计目标：让非开发者不用理解 Docker、uvicorn、venv、环境变量。

.PARAMETER Port
    后端监听端口，默认 8000。
.PARAMETER BindAddress
    后端监听地址，默认 127.0.0.1。院内局域网访问可改为 0.0.0.0。
.PARAMETER SkipInstall
    跳过 pip install -r requirements.txt，适合已完成初始化后的快速启动。
.PARAMETER NoBrowser
    不自动打开浏览器。
.PARAMETER AllowNoOllama
    Ollama 未响应时仍继续启动。适合只演示非 AI 业务模块或使用远程 API。
#>

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$BindAddress = '127.0.0.1',
    [switch]$SkipInstall,
    [switch]$NoBrowser,
    [switch]$AllowNoOllama
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Step([string]$Message) {
    Write-Host "\n==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) {
    Write-Host "  OK  $Message" -ForegroundColor Green
}

function Write-Fix([string]$Message) {
    Write-Host "  FIX $Message" -ForegroundColor Yellow
}

function Test-CommandExists([string]$Command) {
    return [bool](Get-Command $Command -ErrorAction SilentlyContinue)
}

function Test-PortFree([int]$TargetPort) {
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $TargetPort)
        $listener.Start()
        $listener.Stop()
        return $true
    } catch {
        return $false
    }
}

function Load-DotEnv([string]$EnvPath) {
    if (-not (Test-Path $EnvPath)) { return }
    Get-Content $EnvPath | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { return }
        $key = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim()
        if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        if (-not [Environment]::GetEnvironmentVariable($key, 'Process')) {
            [Environment]::SetEnvironmentVariable($key, $val, 'Process')
        }
    }
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

Write-Host '智护银伴 · 本地应用化启动器' -ForegroundColor White
Write-Host "项目目录: $ProjectDir" -ForegroundColor DarkGray

Write-Step '检查基础环境'
if (-not (Test-CommandExists 'python')) {
    throw '未找到 python。请先安装 Python 3.11+，或通过 winget install Python.Python.3.11 安装。'
}
$pythonVersion = (& python --version 2>&1).ToString()
Write-Ok $pythonVersion

if (-not (Test-Path 'requirements.txt')) {
    throw '未找到 requirements.txt，请确认在 Zhihu-Yinban 项目根目录运行。'
}

Write-Step '准备 Python 虚拟环境'
$venvDir = Join-Path $ProjectDir 'venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$venvActivate = Join-Path $venvDir 'Scripts\Activate.ps1'
if (-not (Test-Path $venvPython)) {
    Write-Fix '未发现 venv，正在创建虚拟环境...'
    & python -m venv $venvDir
}
. $venvActivate
Write-Ok 'venv 已激活'

if (-not $SkipInstall) {
    Write-Step '安装/校验依赖'
    & python -m pip install --upgrade pip
    & python -m pip install -r requirements.txt
    Write-Ok '依赖安装完成'
} else {
    Write-Step '跳过依赖安装'
    Write-Ok '已按参数 SkipInstall 跳过 pip install'
}

Write-Step '初始化配置文件'
$envPath = Join-Path $ProjectDir '.env'
$envExample = Join-Path $ProjectDir '.env.example'
if (-not (Test-Path $envPath)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envPath
        Write-Fix '.env 不存在，已从 .env.example 复制。首次生产部署请修改 AUTH_TOKEN / PII_ENCRYPTION_KEY 等敏感配置。'
    } else {
        @"
AUTH_TOKEN=change-me-before-production
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL_NAME=huatuo_o1_7b
EMBEDDING_ALLOW_DEGRADED=true
"@ | Set-Content -Encoding UTF8 $envPath
        Write-Fix '.env.example 不存在，已生成最小 .env。生产部署请务必修改默认密钥。'
    }
} else {
    Write-Ok '.env 已存在'
}
Load-DotEnv $envPath

Write-Step '检查 Ollama 可用性'
$ollamaOk = $false
try {
    $resp = Invoke-WebRequest -Uri 'http://localhost:11434/' -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
    if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) { $ollamaOk = $true }
} catch {}
if ($ollamaOk) {
    Write-Ok 'Ollama 本地服务已响应 http://localhost:11434'
} elseif ($AllowNoOllama -or $env:LLM_PROVIDER -eq 'openai') {
    Write-Fix 'Ollama 未响应，但已允许继续启动。AI 本地推理可能不可用。'
} else {
    Write-Fix 'Ollama 未响应。可先打开 Ollama 应用或运行 ollama serve。'
    Write-Host '  若只想启动非 AI 业务模块，可加参数：-AllowNoOllama' -ForegroundColor Yellow
    throw 'Ollama 未启动，已停止。'
}

Write-Step '检查端口'
if (-not (Test-PortFree $Port)) {
    Write-Host "端口 $Port 已被占用。排查命令：" -ForegroundColor Yellow
    Write-Host "  Get-NetTCPConnection -LocalPort $Port | Select-Object -ExpandProperty OwningProcess | % { Get-Process -Id `$_ }"
    throw "端口 $Port 不可用。"
}
Write-Ok "端口 $Port 可用"

Write-Step '启动后端服务'
$logsDir = Join-Path $ProjectDir 'logs'
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$logFile = Join-Path $logsDir 'launcher-backend.log'
$arguments = @('-m', 'uvicorn', 'main:app', '--host', $BindAddress, '--port', $Port)
$process = Start-Process -FilePath (Join-Path $venvDir 'Scripts\python.exe') -ArgumentList $arguments -WorkingDirectory $ProjectDir -PassThru -RedirectStandardOutput $logFile -RedirectStandardError $logFile -WindowStyle Hidden
Write-Ok "后端进程已启动 PID=$($process.Id)，日志: $logFile"

Write-Step '等待服务健康检查'
$healthUrl = "http://127.0.0.1:$Port/health"
$ready = $false
for ($i = 1; $i -le 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2 -ErrorAction Stop
        if ($health.status -eq 'ok') {
            $ready = $true
            Write-Ok "服务已就绪：$healthUrl"
            Write-Host "  auth_mode: $($health.auth_mode)" -ForegroundColor DarkGray
            Write-Host "  rag_available: $($health.rag_available)" -ForegroundColor DarkGray
            Write-Host "  pii_encryption_enabled: $($health.pii_encryption_enabled)" -ForegroundColor DarkGray
            break
        }
    } catch {
        if ($process.HasExited) { break }
    }
}

if (-not $ready) {
    Write-Host "服务未能在预期时间内就绪，请查看日志：$logFile" -ForegroundColor Red
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    exit 1
}

$appUrl = "http://127.0.0.1:$Port/"
$nurseUrl = "http://127.0.0.1:$Port/nurse"
Write-Host "\n启动完成" -ForegroundColor Green
Write-Host "  管理端: $appUrl"
Write-Host "  护工端: $nurseUrl"
Write-Host "  健康检查: $healthUrl"
Write-Host "  停止服务: Stop-Process -Id $($process.Id)"

if (-not $NoBrowser) {
    Start-Process $appUrl
}
