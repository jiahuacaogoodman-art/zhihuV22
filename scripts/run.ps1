#Requires -Version 5.1
<#
.SYNOPSIS
    智护银伴 · Windows 裸机本地启动（不走 Docker）
.DESCRIPTION
    对应 scripts/run.sh 的 Windows 实现：
      - 自动激活 venv（PowerShell 路径）
      - 检查 Ollama 服务（Windows 服务 / 本地端口）
      - 启动 uvicorn

    适合开发场景。生产推荐用 setup.ps1 + Docker Compose。
#>

[CmdletBinding()]
param(
    [string]$BindAddress = '0.0.0.0',
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir  = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

# 激活 venv（如果存在）
$venvActivate = Join-Path $ProjectDir 'venv\Scripts\Activate.ps1'
if (Test-Path $venvActivate) {
    Write-Host '正在激活 Python 虚拟环境...' -ForegroundColor Cyan
    . $venvActivate
} else {
    Write-Warning '未找到虚拟环境 (venv\Scripts\Activate.ps1)，将使用系统 Python'
    Write-Host '建议先创建虚拟环境：'
    Write-Host '  python -m venv venv'
    Write-Host '  .\venv\Scripts\Activate.ps1'
    Write-Host '  pip install -r requirements.txt'
    Write-Host ''
}

# 检查 .env
if (-not (Test-Path '.env')) {
    Write-Warning '.env 文件不存在'
    Write-Host '  建议先运行：.\scripts\setup.ps1'
    Write-Host '  或手动创建：copy .env.example .env'
    Write-Host ''
}

# 检查 Ollama（端口监听比进程名更可靠 —— Windows 上 Ollama 进程名是 ollama.exe，
# 但 ollama serve 启动后名字可能不同）
Write-Host '检查 Ollama 服务...' -ForegroundColor Cyan
$ollamaOk = $false
try {
    $resp = Invoke-WebRequest -Uri 'http://localhost:11434/' -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
    if ($resp.StatusCode -eq 200) {
        $ollamaOk = $true
        Write-Host '  Ollama 服务正常 (localhost:11434)' -ForegroundColor Green
    }
} catch {}

if (-not $ollamaOk) {
    Write-Warning 'Ollama 服务未响应（http://localhost:11434）'
    Write-Host ''
    Write-Host '  请先启动 Ollama：'
    Write-Host '    1) 装了 Ollama for Windows：开始菜单打开 Ollama 应用'
    Write-Host '    2) 或命令行：ollama serve'
    Write-Host '    3) 或安装：winget install Ollama.Ollama'
    Write-Host ''
    $continue = Read-Host '  继续启动? (没 Ollama 时 AI 功能会失败) [y/N]'
    if ($continue -notmatch '^[yY]') {
        exit 1
    }
}

# 检查端口可用
try {
    $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $Port)
    $l.Start(); $l.Stop()
} catch {
    Write-Warning "端口 $Port 已被占用"
    Write-Host '  查看占用进程：'
    Write-Host "    Get-NetTCPConnection -LocalPort $Port | Select-Object -ExpandProperty OwningProcess | %{ Get-Process -Id `$_ }"
    exit 1
}

# 启动
Write-Host ''
Write-Host "正在启动 智护银伴 后端服务..." -ForegroundColor Cyan
Write-Host "  监听: http://${BindAddress}:${Port}" -ForegroundColor White
Write-Host '  按 CTRL+C 停止' -ForegroundColor White
Write-Host ''

& uvicorn main:app --host $BindAddress --port $Port
