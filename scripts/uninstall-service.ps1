#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    注销 智护银伴 的 Windows 计划任务（开机自启）
.PARAMETER TaskName
    任务名，默认 ZhihuYinban
.PARAMETER StopContainers
    同时停止当前正在跑的 docker compose 容器
#>

[CmdletBinding()]
param(
    [string]$TaskName = 'ZhihuYinban',
    [switch]$StopContainers
)

$ErrorActionPreference = 'Stop'

# 必须管理员
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object System.Security.Principal.WindowsPrincipal($currentUser)
if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error '此脚本需要管理员权限。'
    exit 1
}

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir  = Split-Path -Parent $ScriptDir

# 卸载任务
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "$([char]0x2714) 已删除计划任务：$TaskName" -ForegroundColor Green
} else {
    Write-Host "$([char]0x2139) 计划任务不存在：$TaskName" -ForegroundColor Cyan
}

# 删除包装脚本
$startScript = Join-Path $ProjectDir 'scripts\_service-start.ps1'
if (Test-Path $startScript) {
    Remove-Item -Path $startScript -Force
    Write-Host '已删除启动脚本' -ForegroundColor Green
}

if ($StopContainers) {
    Set-Location $ProjectDir
    Write-Host '正在停止 docker compose 服务...' -ForegroundColor Cyan
    & docker compose down
    Write-Host "$([char]0x2714) 服务已停止（数据卷保留）" -ForegroundColor Green
}
