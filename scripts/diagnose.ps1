#Requires -Version 5.1
<#
.SYNOPSIS
    智护银伴 · Windows 部署诊断脚本
.DESCRIPTION
    收集本地部署常见故障信息：Python、pip、venv、.env、端口、Ollama、Docker、磁盘、/health。
    默认只输出脱敏摘要；加 -WriteReport 可生成 logs/diagnose-*.txt 报告。
#>

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$WriteReport
)

$ErrorActionPreference = 'Continue'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

$lines = New-Object System.Collections.Generic.List[string]
function Add-Line([string]$Text) {
    $lines.Add($Text) | Out-Null
    Write-Host $Text
}
function Mask-Value([string]$Key, [string]$Value) {
    if ($Key -match 'TOKEN|KEY|SECRET|PASSWORD|PASS|AUTH') {
        if ([string]::IsNullOrWhiteSpace($Value)) { return '<empty>' }
        if ($Value.Length -le 6) { return '******' }
        return ($Value.Substring(0, 3) + '***' + $Value.Substring($Value.Length - 3))
    }
    return $Value
}

Add-Line '=== 智护银伴 Windows 部署诊断 ==='
Add-Line "Time: $(Get-Date -Format s)"
Add-Line "ProjectDir: $ProjectDir"
Add-Line "OS: $([System.Environment]::OSVersion.VersionString)"
Add-Line "PowerShell: $($PSVersionTable.PSVersion)"
Add-Line ''

Add-Line '--- Python ---'
try { Add-Line "python: $((& python --version 2>&1).ToString())" } catch { Add-Line "python: NOT FOUND - $($_.Exception.Message)" }
try { Add-Line "pip: $((& python -m pip --version 2>&1).ToString())" } catch { Add-Line "pip: ERROR - $($_.Exception.Message)" }
$venvPython = Join-Path $ProjectDir 'venv\Scripts\python.exe'
Add-Line "venv python exists: $(Test-Path $venvPython)"
if (Test-Path $venvPython) {
    try { Add-Line "venv python: $((& $venvPython --version 2>&1).ToString())" } catch {}
}
Add-Line ''

Add-Line '--- Files ---'
@('main.py', 'requirements.txt', '.env', '.env.example', 'static\index.html', 'static\nurse.html') | ForEach-Object {
    Add-Line "$($_): $(Test-Path (Join-Path $ProjectDir $_))"
}
Add-Line ''

Add-Line '--- .env keys (masked) ---'
$envPath = Join-Path $ProjectDir '.env'
if (Test-Path $envPath) {
    Get-Content $envPath | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) { return }
        $eq = $line.IndexOf('=')
        if ($eq -lt 1) { return }
        $key = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        Add-Line "$key=$(Mask-Value $key $value)"
    }
} else {
    Add-Line '.env missing'
}
Add-Line ''

Add-Line '--- Port ---'
try {
    $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    if ($connections) {
        foreach ($conn in $connections) {
            $proc = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue
            Add-Line "port $Port used by PID=$($conn.OwningProcess) process=$($proc.ProcessName) state=$($conn.State)"
        }
    } else {
        Add-Line "port $Port appears free"
    }
} catch {
    Add-Line "port check error: $($_.Exception.Message)"
}
Add-Line ''

Add-Line '--- Ollama ---'
try {
    $resp = Invoke-WebRequest -Uri 'http://localhost:11434/' -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
    Add-Line "ollama http status: $($resp.StatusCode)"
} catch {
    Add-Line "ollama not responding: $($_.Exception.Message)"
}
try {
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Add-Line "ollama version: $((& ollama --version 2>&1).ToString())"
        Add-Line 'ollama models:'
        (& ollama list 2>&1) | ForEach-Object { Add-Line "  $_" }
    } else {
        Add-Line 'ollama command: NOT FOUND'
    }
} catch {
    Add-Line "ollama cli error: $($_.Exception.Message)"
}
Add-Line ''

Add-Line '--- Docker ---'
try {
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        Add-Line "docker: $((& docker --version 2>&1).ToString())"
        Add-Line "docker compose: $((& docker compose version 2>&1).ToString())"
    } else {
        Add-Line 'docker command: NOT FOUND'
    }
} catch {
    Add-Line "docker error: $($_.Exception.Message)"
}
Add-Line ''

Add-Line '--- Disk ---'
try {
    $drive = Get-PSDrive -Name (Get-Location).Drive.Name
    Add-Line "drive $($drive.Name): free=$([math]::Round($drive.Free/1GB,2))GB used=$([math]::Round($drive.Used/1GB,2))GB"
} catch {}
Add-Line ''

Add-Line '--- Health endpoint ---'
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2 -ErrorAction Stop
    ($health | ConvertTo-Json -Depth 5) -split "`n" | ForEach-Object { Add-Line $_ }
} catch {
    Add-Line "health not available: $($_.Exception.Message)"
}
Add-Line ''

if ($WriteReport) {
    $logsDir = Join-Path $ProjectDir 'logs'
    New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
    $report = Join-Path $logsDir ("diagnose-{0}.txt" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    $lines | Set-Content -Encoding UTF8 $report
    Write-Host "\n诊断报告已生成: $report" -ForegroundColor Green
}
