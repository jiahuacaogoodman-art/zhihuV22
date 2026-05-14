#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    把 智护银伴 注册为 Windows 计划任务实现开机自启
.DESCRIPTION
    Windows 上替代 systemd 的最稳妥方案是 Scheduled Task（Win10/11/Server 全支持，
    无需第三方工具如 NSSM）。

    本脚本创建一个开机触发的计划任务：
      触发器：系统启动后 30 秒（等 Docker Desktop 起来）
      动作：  docker compose up -d
      重试：  失败后每 1 分钟重试，共 3 次

    需要管理员权限运行。

.PARAMETER TaskName
    任务名，默认 ZhihuYinban
.PARAMETER User
    运行任务的用户，默认 SYSTEM（系统级，不依赖某个人登录）
    注意：SYSTEM 用户访问不到当前用户的 Docker Desktop，
    需要让 Docker Desktop 设置为 "Start Docker Desktop when you log in"，
    任务在用户登录后再触发更可靠 —— 默认改为 AtLogon。

.EXAMPLE
    # 管理员 PowerShell
    .\scripts\install-service.ps1
.EXAMPLE
    .\scripts\install-service.ps1 -TaskName MyYinban
#>

[CmdletBinding()]
param(
    [string]$TaskName = 'ZhihuYinban',
    [ValidateSet('AtLogon', 'AtStartup')]
    [string]$Trigger = 'AtLogon'
)

$ErrorActionPreference = 'Stop'

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir  = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

# 必须是管理员
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object System.Security.Principal.WindowsPrincipal($currentUser)
if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error '此脚本需要管理员权限。请在 "管理员 PowerShell" 中运行。'
    exit 1
}

# 检查前置条件
if (-not (Test-Path (Join-Path $ProjectDir 'docker-compose.yml'))) {
    Write-Error "未找到 docker-compose.yml（当前目录: $ProjectDir）"
    exit 1
}
if (-not (Test-Path (Join-Path $ProjectDir '.env'))) {
    Write-Error '.env 文件不存在，请先运行 .\scripts\setup.ps1'
    exit 1
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error 'Docker 未安装'
    exit 1
}

# 删除已有任务
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "正在删除现有任务 $TaskName ..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# 准备启动脚本（包装一层 PowerShell 以便记录日志）
$startScript = Join-Path $ProjectDir 'scripts\_service-start.ps1'
$logFile     = Join-Path $ProjectDir 'service.log'

@"
# 自动生成 - 由 install-service.ps1 创建
# 修改本文件请重跑 install-service.ps1
`$ErrorActionPreference = 'Continue'
Set-Location '$ProjectDir'

`$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
"[`$ts] Starting ZhihuYinban via Docker Compose..." | Out-File -FilePath '$logFile' -Append -Encoding UTF8

# 等 Docker daemon 就绪（最多 5 分钟）
`$dockerReady = `$false
for (`$i = 0; `$i -lt 60; `$i++) {
    try {
        docker info --format '{{.ServerVersion}}' 2>`$null | Out-Null
        if (`$LASTEXITCODE -eq 0) { `$dockerReady = `$true; break }
    } catch {}
    Start-Sleep -Seconds 5
}

if (-not `$dockerReady) {
    "[`$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ERROR: Docker daemon not ready after 5 minutes" | Out-File -FilePath '$logFile' -Append -Encoding UTF8
    exit 1
}

# 读 .env 决定是否启用 ollama profile
`$envText = Get-Content -Path '.env' -Raw -Encoding UTF8
`$profileArgs = @()
if (`$envText -match '(?m)^LLM_PROVIDER=ollama' -or `$envText -notmatch '(?m)^LLM_PROVIDER=') {
    `$profileArgs = @('--profile', 'ollama')
}

# 启动
& docker compose @profileArgs up -d 2>&1 | Out-File -FilePath '$logFile' -Append -Encoding UTF8

if (`$LASTEXITCODE -eq 0) {
    "[`$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ZhihuYinban started successfully" | Out-File -FilePath '$logFile' -Append -Encoding UTF8
} else {
    "[`$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ERROR: docker compose up failed" | Out-File -FilePath '$logFile' -Append -Encoding UTF8
}
"@ | Set-Content -Path $startScript -Encoding UTF8

Write-Host ''
Write-Host '正在注册 Windows 计划任务...' -ForegroundColor Cyan

# 触发器
$triggerObj = if ($Trigger -eq 'AtLogon') {
    New-ScheduledTaskTrigger -AtLogOn
} else {
    # 系统启动后延迟 60 秒（等 Docker Desktop 起来）
    $t = New-ScheduledTaskTrigger -AtStartup
    $t.Delay = 'PT60S'
    $t
}

# 动作：用 PowerShell 跑包装脚本
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`"" `
    -WorkingDirectory $ProjectDir

# 设置：失败重试，不限运行时长
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

# 当前用户身份（任务跑在用户上下文，能访问 Docker Desktop）
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Highest

# 注册
Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $triggerObj `
    -Action $action `
    -Settings $settings `
    -Principal $principal `
    -Description '智护银伴 - 开机自启 Docker Compose 服务' | Out-Null

Write-Host ''
Write-Host "$([char]0x2714) 计划任务已创建：$TaskName" -ForegroundColor Green
Write-Host ''
Write-Host '  触发条件：' -NoNewline
if ($Trigger -eq 'AtLogon') {
    Write-Host "用户 $env:USERNAME 登录时" -ForegroundColor White
} else {
    Write-Host '系统启动 60 秒后' -ForegroundColor White
}
Write-Host '  日志位置：' -NoNewline; Write-Host $logFile -ForegroundColor Cyan
Write-Host ''
Write-Host '  管理命令：' -ForegroundColor White
Write-Host "    立即运行： Start-ScheduledTask -TaskName $TaskName"
Write-Host "    查看状态： Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "    查看日志： Get-Content '$logFile' -Tail 50"
Write-Host "    卸载任务： .\scripts\uninstall-service.ps1"
Write-Host ''

# 立即测试启动
$testNow = Read-Host '是否立即触发一次启动测试? [Y/n]'
if ($testNow -notmatch '^[nN]') {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host '已触发，10 秒后查看日志...' -ForegroundColor Cyan
    Start-Sleep -Seconds 10
    if (Test-Path $logFile) {
        Get-Content -Path $logFile -Tail 10
    }
}
