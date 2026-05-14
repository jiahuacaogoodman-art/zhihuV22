#Requires -Version 5.1
<#
.SYNOPSIS
    把前端依赖的 CDN 资源下载到本地 static\vendor\，让系统完全离线可用
.DESCRIPTION
    对应 scripts/fetch_vendors.sh 的 Windows 实现，使用 PowerShell 原生 Invoke-WebRequest
    （不依赖 curl）。
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir  = Split-Path -Parent $ScriptDir
$VendorDir   = Join-Path $ProjectDir 'static\vendor'

if (-not (Test-Path $VendorDir)) {
    New-Item -ItemType Directory -Path $VendorDir -Force | Out-Null
}

$assets = @(
    @{ Name = 'lucide.min.js';      Url = 'https://unpkg.com/lucide@0.454.0/dist/umd/lucide.min.js' },
    @{ Name = 'gsap.min.js';        Url = 'https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js' },
    @{ Name = 'ScrollTrigger.min.js'; Url = 'https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js' },
    @{ Name = 'lottie-player.js';   Url = 'https://cdn.jsdelivr.net/npm/@lottiefiles/lottie-player@2.0.8/dist/lottie-player.js' }
)

foreach ($a in $assets) {
    $out = Join-Path $VendorDir $a.Name
    Write-Host ('  -> {0}' -f $a.Name) -ForegroundColor Cyan
    try {
        Invoke-WebRequest -Uri $a.Url -OutFile $out -UseBasicParsing -ErrorAction Stop
    } catch {
        Write-Warning ('下载失败 {0}: {1}' -f $a.Name, $_.Exception.Message)
        Write-Host '  如果是网络问题，请配置代理：' -ForegroundColor Yellow
        Write-Host '    $env:HTTP_PROXY="http://127.0.0.1:7890"' -ForegroundColor DarkGray
        Write-Host '    $env:HTTPS_PROXY="http://127.0.0.1:7890"' -ForegroundColor DarkGray
        exit 1
    }
}

Write-Host ''
Write-Host '完成。把 static\design\vendors.js 里的 URL 前缀改为 /static/vendor/ 即可离线运行。' -ForegroundColor Green
