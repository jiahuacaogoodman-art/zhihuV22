#Requires -Version 5.1
<#
.SYNOPSIS
    智护银伴 Windows 环境诊断（独立可调用）
.DESCRIPTION
    在不做任何修改的前提下，逐项检查 Windows 部署环境，给出具体的修复建议。
    可由 setup.ps1 在第一步调用，也可独立运行排查问题。

    检查项：
      1. PowerShell 版本（>= 5.1）
      2. 执行策略
      3. Docker / Docker Desktop / WSL2 backend
      4. Docker Compose V2
      5. Docker daemon 是否运行
      6. NVIDIA GPU + WSL2 GPU 直通
      7. 端口占用（8000 / 11434）
      8. Hyper-V 保留端口段冲突
      9. 长路径支持（LongPathsEnabled）
     10. 防火墙状态
     11. 网络代理（HTTP_PROXY / WinINet）
     12. 行尾规范（git core.autocrlf）
     13. 磁盘空间（C: 至少 30 GB）
     14. 系统内存（至少 16 GB）

.PARAMETER Quick
    跳过耗时检查（GPU 探测、端口扫描）
.PARAMETER Json
    以 JSON 输出，便于自动化消费
.EXAMPLE
    .\scripts\preflight.ps1
.EXAMPLE
    .\scripts\preflight.ps1 -Json
#>

[CmdletBinding()]
param(
    [switch]$Quick,
    [switch]$Json
)

$ErrorActionPreference = 'Continue'  # 诊断脚本不应因单个项失败而中断
$WarningPreference = 'SilentlyContinue'

# ── 颜色 / 输出 ─────────────────────────────────────────────
$script:Results = [System.Collections.Generic.List[object]]::new()

function Add-Result {
    param(
        [string]$Item,
        [ValidateSet('OK', 'WARN', 'FAIL', 'INFO')]
        [string]$Status,
        [string]$Detail = '',
        [string]$Fix = ''
    )
    $script:Results.Add([PSCustomObject]@{
        Item   = $Item
        Status = $Status
        Detail = $Detail
        Fix    = $Fix
    })
}

function Write-CheckLine {
    param(
        [string]$Item,
        [string]$Status,
        [string]$Detail
    )
    if ($Json) { return }

    $icon = switch ($Status) {
        'OK'   { "$([char]0x2714)" ; $color = 'Green' }
        'WARN' { "$([char]0x26A0)" ; $color = 'Yellow' }
        'FAIL' { "$([char]0x2718)" ; $color = 'Red' }
        'INFO' { "$([char]0x2139)" ; $color = 'Cyan' }
        default { '?'              ; $color = 'White' }
    }
    Write-Host ("  {0,-2} {1,-32}" -f $icon, $Item) -NoNewline -ForegroundColor $color
    if ($Detail) { Write-Host " $Detail" -ForegroundColor DarkGray } else { Write-Host '' }
}

function Write-Section {
    param([string]$Title)
    if ($Json) { return }
    Write-Host ''
    Write-Host "$([char]0x2500)$([char]0x2500)$([char]0x2500) $Title $([char]0x2500)$([char]0x2500)$([char]0x2500)" -ForegroundColor Cyan
}

# ── 工具函数 ─────────────────────────────────────────────────
function Test-CommandExists {
    param([string]$Name)
    $null -ne (Get-Command -Name $Name -ErrorAction SilentlyContinue)
}

function Get-DockerInfo {
    if (-not (Test-CommandExists 'docker')) { return $null }
    try {
        $info = docker info --format '{{json .}}' 2>$null | ConvertFrom-Json -ErrorAction SilentlyContinue
        return $info
    } catch {
        return $null
    }
}

# ============================================================
# 开始检查
# ============================================================
if (-not $Json) {
    Write-Host ''
    Write-Host '  ' -NoNewline
    Write-Host '智护银伴 · Windows 环境诊断' -ForegroundColor Cyan
    Write-Host ''
}

# ── 1. PowerShell 版本 ──────────────────────────────────────
Write-Section 'PowerShell 与执行策略'

$psVer = $PSVersionTable.PSVersion
if ($psVer.Major -ge 5) {
    Add-Result 'PowerShell 版本' 'OK' "v$psVer"
    Write-CheckLine 'PowerShell 版本' 'OK' "v$psVer"
} else {
    Add-Result 'PowerShell 版本' 'FAIL' "v$psVer (需要 5.1+)" `
        '安装 PowerShell 7: winget install Microsoft.PowerShell'
    Write-CheckLine 'PowerShell 版本' 'FAIL' "v$psVer (需要 5.1+)"
}

# 执行策略（不强求 Bypass，但要能跑当前脚本）
$execPolicy = Get-ExecutionPolicy -Scope CurrentUser
if ($execPolicy -in @('Restricted', 'AllSigned')) {
    Add-Result '执行策略' 'WARN' $execPolicy `
        '一次性绕过：powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1'
    Write-CheckLine '执行策略 (CurrentUser)' 'WARN' "$execPolicy (脚本可能无法运行)"
} else {
    Add-Result '执行策略' 'OK' $execPolicy
    Write-CheckLine '执行策略 (CurrentUser)' 'OK' $execPolicy
}

# ── 2. Docker ───────────────────────────────────────────────
Write-Section 'Docker 与 Compose'

$dockerOk = $false
if (Test-CommandExists 'docker') {
    try {
        $dockerVersion = (docker --version 2>$null) -join ''
        Add-Result 'Docker' 'OK' $dockerVersion
        Write-CheckLine 'Docker CLI' 'OK' $dockerVersion
        $dockerOk = $true
    } catch {
        Add-Result 'Docker' 'FAIL' '已安装但调用失败' '重启 Docker Desktop'
        Write-CheckLine 'Docker CLI' 'FAIL' '已安装但调用失败'
    }
} else {
    Add-Result 'Docker' 'FAIL' '未安装' `
        'winget install Docker.DockerDesktop  或访问 https://www.docker.com/products/docker-desktop/'
    Write-CheckLine 'Docker CLI' 'FAIL' '未安装'
}

# Docker Compose V2
if ($dockerOk) {
    $composeOk = $false
    try {
        $composeVer = (docker compose version --short 2>$null) -join ''
        if ($LASTEXITCODE -eq 0 -and $composeVer) {
            Add-Result 'Docker Compose V2' 'OK' "v$composeVer"
            Write-CheckLine 'Docker Compose V2' 'OK' "v$composeVer"
            $composeOk = $true
        }
    } catch {}

    if (-not $composeOk) {
        if (Test-CommandExists 'docker-compose') {
            $composeV1 = (docker-compose --version 2>$null) -join ''
            Add-Result 'Docker Compose' 'WARN' "$composeV1 (V1 已 EOL)" `
                '升级 Docker Desktop 到最新版即自带 Compose V2'
            Write-CheckLine 'Docker Compose' 'WARN' 'V1 已停止维护'
        } else {
            Add-Result 'Docker Compose' 'FAIL' '未安装' `
                '升级 Docker Desktop 到最新版（自带 Compose V2）'
            Write-CheckLine 'Docker Compose' 'FAIL' '未安装'
        }
    }
}

# Docker daemon
if ($dockerOk) {
    $info = Get-DockerInfo
    if ($null -eq $info) {
        Add-Result 'Docker daemon' 'FAIL' '未运行' `
            '启动 Docker Desktop（系统托盘鲸鱼图标）→ 等待至显示 "Docker Desktop is running"'
        Write-CheckLine 'Docker daemon' 'FAIL' '未运行 (启动 Docker Desktop)'
    } else {
        Add-Result 'Docker daemon' 'OK' "OS=$($info.OperatingSystem) Arch=$($info.Architecture)"
        Write-CheckLine 'Docker daemon' 'OK' "$($info.OperatingSystem)"

        # WSL2 backend 检测
        $isWsl = $info.OperatingSystem -match 'WSL' -or $info.KernelVersion -match 'WSL'
        if ($isWsl) {
            Add-Result 'Docker 后端' 'OK' 'WSL2 (推荐)'
            Write-CheckLine 'Docker 后端' 'OK' 'WSL2 (推荐)'
        } elseif ($info.OperatingSystem -match 'Hyper-V') {
            Add-Result 'Docker 后端' 'WARN' 'Hyper-V (建议切到 WSL2)' `
                'Docker Desktop → Settings → General → Use WSL 2 based engine'
            Write-CheckLine 'Docker 后端' 'WARN' 'Hyper-V (推荐切到 WSL2)'
        } else {
            Add-Result 'Docker 后端' 'INFO' $info.OperatingSystem
            Write-CheckLine 'Docker 后端' 'INFO' $info.OperatingSystem
        }
    }
}

# ── 3. NVIDIA GPU 检测（仅 WSL2 backend 有效）─────────────────
if (-not $Quick) {
    Write-Section 'GPU 加速（可选）'

    $gpuFound = $false

    # Method 1: nvidia-smi.exe in PATH (Win drivers)
    if (Test-CommandExists 'nvidia-smi') {
        try {
            $gpuLine = (nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null | Select-Object -First 1)
            if ($gpuLine) {
                Add-Result 'NVIDIA GPU (主机)' 'OK' $gpuLine.Trim()
                Write-CheckLine 'NVIDIA GPU (主机)' 'OK' $gpuLine.Trim()
                $gpuFound = $true
            }
        } catch {}
    }

    # Method 2: 容器里能否看到 GPU（最关键，决定 Docker 能否用）
    if ($dockerOk -and (Get-DockerInfo)) {
        try {
            $testGpu = docker run --rm --gpus=all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi 2>&1
            if ($LASTEXITCODE -eq 0) {
                Add-Result 'Docker GPU 直通' 'OK' '容器内可见 GPU'
                Write-CheckLine 'Docker GPU 直通' 'OK' '容器内可见 GPU'
                $gpuFound = $true
            } else {
                if ($testGpu -match 'could not select device|nvidia-container-cli|unknown or invalid') {
                    Add-Result 'Docker GPU 直通' 'WARN' '检测到 GPU 但容器无法访问' `
                        '1) 在 Windows 装最新 NVIDIA 驱动（含 WSL2 支持，>=470）；2) Docker Desktop → Settings → Resources → WSL Integration 打开'
                    Write-CheckLine 'Docker GPU 直通' 'WARN' '驱动或 Docker 设置问题'
                }
            }
        } catch {
            # docker run 失败通常是没 GPU，正常
        }
    }

    if (-not $gpuFound) {
        Add-Result 'GPU' 'INFO' '未检测到 NVIDIA GPU (CPU 推理可用)'
        Write-CheckLine 'NVIDIA GPU' 'INFO' '未检测到 (CPU 推理可用)'
    }
}

# ── 4. 端口占用 + Hyper-V 保留端口段 ─────────────────────────
Write-Section '端口可用性'

function Test-PortFree {
    param([int]$Port)
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        $listener.Stop()
        return $true
    } catch {
        return $false
    }
}

# Hyper-V 保留端口段（Windows 8000-8999 偶尔被吞）
$hyperVReserved = @()
try {
    $reservedRanges = & netsh interface ipv4 show excludedportrange protocol=tcp 2>$null
    foreach ($line in $reservedRanges) {
        if ($line -match '^\s*(\d+)\s+(\d+)\s*$') {
            $start = [int]$Matches[1]
            $end = [int]$Matches[2]
            $hyperVReserved += [PSCustomObject]@{ Start = $start; End = $end }
        }
    }
} catch {}

function Test-PortReserved {
    param([int]$Port)
    foreach ($r in $hyperVReserved) {
        if ($Port -ge $r.Start -and $Port -le $r.End) { return $true }
    }
    return $false
}

# 端口 8000（FastAPI）
$port8000Free = Test-PortFree -Port 8000
$port8000Reserved = Test-PortReserved -Port 8000
if ($port8000Reserved) {
    Add-Result '端口 8000' 'FAIL' '被 Hyper-V 保留' `
        '管理员 PowerShell 执行：net stop winnat; netsh int ipv4 add excludedportrange protocol=tcp startport=8000 numberofports=1; net start winnat  或者改用其它端口（编辑 .env 的 PORT）'
    Write-CheckLine '端口 8000' 'FAIL' '被 Hyper-V 保留段占用'
} elseif (-not $port8000Free) {
    Add-Result '端口 8000' 'WARN' '已被占用' `
        "查看占用进程：Get-NetTCPConnection -LocalPort 8000 | Select-Object -ExpandProperty OwningProcess | %{ Get-Process -Id `$_ }"
    Write-CheckLine '端口 8000' 'WARN' '被占用 (可能其它服务在跑)'
} else {
    Add-Result '端口 8000' 'OK' '可用'
    Write-CheckLine '端口 8000' 'OK' '可用'
}

# 端口 11434（Ollama）
$port11434Free = Test-PortFree -Port 11434
$port11434Reserved = Test-PortReserved -Port 11434
if ($port11434Reserved) {
    Add-Result '端口 11434' 'WARN' '被 Hyper-V 保留 (Ollama 容器仍可监听 0.0.0.0)' ''
    Write-CheckLine '端口 11434' 'WARN' '被 Hyper-V 保留'
} elseif (-not $port11434Free) {
    # Ollama 已经在跑也是常见情况，不当成错误
    Add-Result '端口 11434' 'INFO' '已被占用 (可能本机 Ollama 已启动)' ''
    Write-CheckLine '端口 11434' 'INFO' '已被占用 (可能 Ollama 已启动)'
} else {
    Add-Result '端口 11434' 'OK' '可用'
    Write-CheckLine '端口 11434' 'OK' '可用'
}

# ── 5. 文件系统能力 ──────────────────────────────────────────
Write-Section '文件系统'

# 长路径支持（避免 ~/.cache/huggingface 路径过长）
try {
    $longPath = (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -ErrorAction Stop).LongPathsEnabled
    if ($longPath -eq 1) {
        Add-Result '长路径支持' 'OK' 'LongPathsEnabled=1'
        Write-CheckLine '长路径支持' 'OK' '已启用'
    } else {
        Add-Result '长路径支持' 'WARN' 'LongPathsEnabled=0' `
            '管理员 PowerShell：Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -Value 1'
        Write-CheckLine '长路径支持' 'WARN' '未启用 (HF 缓存可能路径超 260 字符)'
    }
} catch {
    Add-Result '长路径支持' 'WARN' '无法读取注册表' ''
    Write-CheckLine '长路径支持' 'WARN' '无法读取'
}

# Git core.autocrlf 检测（防止脚本被 CRLF 污染）
if (Test-CommandExists 'git') {
    try {
        $autocrlf = (git config --get core.autocrlf 2>$null)
        if ($autocrlf -eq 'true' -or $autocrlf -eq 'input') {
            # 我们已经写了 .gitattributes 强制 LF，但提醒一下
            Add-Result 'git core.autocrlf' 'INFO' "$autocrlf (.gitattributes 已强制 LF)" ''
            Write-CheckLine 'git core.autocrlf' 'INFO' "$autocrlf (.gitattributes 接管)"
        } else {
            Add-Result 'git core.autocrlf' 'OK' "$autocrlf"
            Write-CheckLine 'git core.autocrlf' 'OK' "$autocrlf"
        }
    } catch {}
}

# 磁盘空间（C: 至少 30 GB）
try {
    $cDrive = Get-PSDrive -Name C -ErrorAction Stop
    $freeGB = [math]::Round($cDrive.Free / 1GB, 1)
    if ($freeGB -lt 15) {
        Add-Result '磁盘空间 (C:)' 'FAIL' "${freeGB} GB 可用 (< 15 GB)" `
            '清理磁盘 - 模型权重需 ~5GB + Docker 镜像 ~3GB'
        Write-CheckLine '磁盘空间 (C:)' 'FAIL' "${freeGB} GB 可用"
    } elseif ($freeGB -lt 30) {
        Add-Result '磁盘空间 (C:)' 'WARN' "${freeGB} GB 可用 (建议 30 GB)" ''
        Write-CheckLine '磁盘空间 (C:)' 'WARN' "${freeGB} GB 可用"
    } else {
        Add-Result '磁盘空间 (C:)' 'OK' "${freeGB} GB 可用"
        Write-CheckLine '磁盘空间 (C:)' 'OK' "${freeGB} GB 可用"
    }
} catch {}

# ── 6. 系统资源 ──────────────────────────────────────────────
Write-Section '系统资源'

try {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    $totalMemGB = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    if ($totalMemGB -lt 8) {
        Add-Result '系统内存' 'FAIL' "${totalMemGB} GB (< 8 GB 跑不动 7B 模型)" `
            '换更小的模型：编辑 .env 设 OLLAMA_MODEL_NAME=qwen2.5:3b'
        Write-CheckLine '系统内存' 'FAIL' "${totalMemGB} GB"
    } elseif ($totalMemGB -lt 16) {
        Add-Result '系统内存' 'WARN' "${totalMemGB} GB (建议 16 GB+，可用 Q3_K_M 量化)" ''
        Write-CheckLine '系统内存' 'WARN' "${totalMemGB} GB (建议 Q3_K_M 量化)"
    } else {
        Add-Result '系统内存' 'OK' "${totalMemGB} GB"
        Write-CheckLine '系统内存' 'OK' "${totalMemGB} GB"
    }
} catch {}

# ── 7. 网络 / 代理 ───────────────────────────────────────────
Write-Section '网络环境'

# 环境变量代理
$envProxy = $env:HTTP_PROXY, $env:HTTPS_PROXY, $env:http_proxy, $env:https_proxy | Where-Object { $_ }
if ($envProxy) {
    Add-Result '代理 (环境变量)' 'INFO' ($envProxy -join ', ') ''
    Write-CheckLine '代理 (环境变量)' 'INFO' ($envProxy[0])
}

# WinINet 系统代理（Clash for Windows / v2rayN 等图形代理）
try {
    $winInet = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction Stop
    if ($winInet.ProxyEnable -eq 1 -and $winInet.ProxyServer) {
        if (-not $envProxy) {
            Add-Result '代理 (系统)' 'WARN' "$($winInet.ProxyServer) 但环境变量未设" `
                "PowerShell 临时设置：`$env:HTTP_PROXY='http://$($winInet.ProxyServer)' ; `$env:HTTPS_PROXY='http://$($winInet.ProxyServer)'"
            Write-CheckLine '代理 (系统)' 'WARN' "$($winInet.ProxyServer) 但 Docker 看不到"
        } else {
            Add-Result '代理 (系统)' 'OK' $winInet.ProxyServer ''
            Write-CheckLine '代理 (系统)' 'OK' $winInet.ProxyServer
        }
    }
} catch {}

# 防火墙
try {
    $fw = Get-NetFirewallProfile -ErrorAction Stop | Where-Object { $_.Enabled -eq $true }
    if ($fw) {
        Add-Result 'Windows 防火墙' 'INFO' "已启用 $($fw.Count) 个配置文件 (首次绑端口可能弹窗)" ''
        Write-CheckLine 'Windows 防火墙' 'INFO' "已启用 (首次绑端口可能弹窗)"
    }
} catch {}

# ── 8. 输出汇总 ──────────────────────────────────────────────
if ($Json) {
    $script:Results | ConvertTo-Json -Depth 5
    return
}

Write-Host ''
Write-Host "$([char]0x2500)$([char]0x2500)$([char]0x2500) 汇总 $([char]0x2500)$([char]0x2500)$([char]0x2500)" -ForegroundColor Cyan
$failCount = ($script:Results | Where-Object { $_.Status -eq 'FAIL' }).Count
$warnCount = ($script:Results | Where-Object { $_.Status -eq 'WARN' }).Count
$okCount   = ($script:Results | Where-Object { $_.Status -eq 'OK' }).Count

Write-Host ''
Write-Host "  $([char]0x2714) 通过: $okCount" -ForegroundColor Green -NoNewline
Write-Host "  $([char]0x26A0) 警告: $warnCount" -ForegroundColor Yellow -NoNewline
Write-Host "  $([char]0x2718) 失败: $failCount" -ForegroundColor Red

if ($failCount -gt 0) {
    Write-Host ''
    Write-Host '  必须先修复以下 FAIL 项：' -ForegroundColor Red
    $script:Results | Where-Object { $_.Status -eq 'FAIL' } | ForEach-Object {
        Write-Host ''
        Write-Host "    $([char]0x2718) $($_.Item): $($_.Detail)" -ForegroundColor Red
        if ($_.Fix) {
            Write-Host "      $([char]0x2192) $($_.Fix)" -ForegroundColor DarkGray
        }
    }
    Write-Host ''
    exit 1
}

if ($warnCount -gt 0) {
    Write-Host ''
    Write-Host '  以下 WARN 项不影响部署，但建议处理：' -ForegroundColor Yellow
    $script:Results | Where-Object { $_.Status -eq 'WARN' } | ForEach-Object {
        Write-Host "    $([char]0x26A0) $($_.Item): $($_.Detail)" -ForegroundColor Yellow
        if ($_.Fix) {
            Write-Host "      $([char]0x2192) $($_.Fix)" -ForegroundColor DarkGray
        }
    }
}

Write-Host ''
Write-Host '  环境就绪，可以运行 setup.ps1' -ForegroundColor Green
Write-Host ''
exit 0
