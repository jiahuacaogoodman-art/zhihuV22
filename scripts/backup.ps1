#Requires -Version 5.1
<#
.SYNOPSIS
    智护银伴 · Docker 数据卷备份（Windows）
.DESCRIPTION
    把所有业务数据卷打包成单个 .tgz，方便迁移/异地备份。
    包含：
      - ehr_db        ChromaDB 向量库
      - ehr_uploads   病历原图 + OCR
      - auth_data     用户 + API Key
      - audit_log     操作审计
      - nursing_events 护理事件流
    不含 ollama_models（模型权重，可重新下载）

    实现：用一次性 alpine 容器把多个 named volume 挂进来 → tar czf

.PARAMETER OutDir
    备份输出目录，默认 .\backups
.PARAMETER ProjectName
    Docker Compose 项目名前缀，默认从当前目录名推断
    （volume 完整名 = ${ProjectName}_${VolumeName}）

.EXAMPLE
    .\scripts\backup.ps1
    # 输出 .\backups\yinban-backup-2026-05-14_153022.tgz

.EXAMPLE
    .\scripts\backup.ps1 -OutDir D:\Backups
#>

[CmdletBinding()]
param(
    [string]$OutDir = '',
    [string]$ProjectName = ''
)

$ErrorActionPreference = 'Stop'

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir  = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

# 默认 OutDir
if ([string]::IsNullOrWhiteSpace($OutDir)) {
    $OutDir = Join-Path $ProjectDir 'backups'
}
if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir | Out-Null
}

# 推断项目名（docker compose 默认用当前目录名小写、去除特殊字符）
if ([string]::IsNullOrWhiteSpace($ProjectName)) {
    $ProjectName = (Split-Path -Leaf $ProjectDir).ToLower() -replace '[^a-z0-9]', ''
    if ([string]::IsNullOrWhiteSpace($ProjectName)) { $ProjectName = 'zhihuyinban' }
}

# 检测实际存在的 volume（兼容用户改了项目名的情况）
function Resolve-Volume {
    param([string]$Suffix)
    $candidates = @(
        "${ProjectName}_${Suffix}",
        "zhihu-yinban_${Suffix}",
        "zhihuyinban_${Suffix}",
        "yinban_${Suffix}",
        $Suffix
    )
    foreach ($name in $candidates) {
        $exists = docker volume inspect $name 2>$null
        if ($LASTEXITCODE -eq 0) { return $name }
    }
    return $null
}

$volumes = @{
    ehr_db          = Resolve-Volume 'ehr_db'
    ehr_uploads     = Resolve-Volume 'ehr_uploads'
    auth_data       = Resolve-Volume 'auth_data'
    audit_log       = Resolve-Volume 'audit_log'
    nursing_events  = Resolve-Volume 'nursing_events'
}

$missing = $volumes.GetEnumerator() | Where-Object { $null -eq $_.Value }
if ($missing) {
    Write-Warning '以下数据卷未找到（可能未启动过服务）：'
    $missing | ForEach-Object { Write-Host "  - $($_.Key)" -ForegroundColor Yellow }
    Write-Host ''
    Write-Host '  当前所有 volume：'
    docker volume ls --filter 'name=ehr|auth|audit|nursing'
    if (-not ($volumes.Values | Where-Object { $_ })) {
        Write-Error '没有任何业务卷可备份。先运行 docker compose up -d'
        exit 1
    }
}

$timestamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$outFile = Join-Path $OutDir "yinban-backup-$timestamp.tgz"
$outFileLinux = '/dst/' + (Split-Path -Leaf $outFile)

# 构建 docker run 参数
$dockerArgs = @('run', '--rm')
foreach ($kv in $volumes.GetEnumerator()) {
    if ($kv.Value) {
        $dockerArgs += '-v'
        $dockerArgs += "$($kv.Value):/src/$($kv.Key):ro"
    }
}
$dockerArgs += '-v'
# Windows 路径需要转换：在 Docker Desktop 下 alpine 容器会自动接受 Windows 风格的绝对路径
# 用 $OutDir 直接传，Docker Desktop 处理转换
$dockerArgs += "${OutDir}:/dst"
$dockerArgs += @('alpine', 'sh', '-c', "tar czf $outFileLinux -C /src .")

Write-Host ''
Write-Host '正在备份业务数据卷...' -ForegroundColor Cyan
Write-Host "  输出: $outFile" -ForegroundColor White
Write-Host ''
Write-Host "  > docker $($dockerArgs -join ' ')" -ForegroundColor DarkGray
Write-Host ''

& docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error '备份失败'
    exit 1
}

if (Test-Path $outFile) {
    $size = (Get-Item $outFile).Length / 1MB
    Write-Host ''
    Write-Host "$([char]0x2714) 备份成功" -ForegroundColor Green
    Write-Host ('  文件: {0}' -f $outFile)
    Write-Host ('  大小: {0:N2} MB' -f $size)
    Write-Host ''
    Write-Host '  恢复方法：' -ForegroundColor White
    Write-Host '    1. docker compose down -v  (删除现有卷)'
    Write-Host '    2. docker compose up -d --no-start  (创建空卷)'
    Write-Host '    3. docker run --rm \'
    Write-Host "         -v zhihu-yinban_ehr_db:/dst/ehr_db \\"
    Write-Host "         -v zhihu-yinban_ehr_uploads:/dst/ehr_uploads \\"
    Write-Host "         -v zhihu-yinban_auth_data:/dst/auth_data \\"
    Write-Host "         -v zhihu-yinban_audit_log:/dst/audit_log \\"
    Write-Host "         -v zhihu-yinban_nursing_events:/dst/nursing_events \\"
    Write-Host "         -v ${OutDir}:/src \\"
    Write-Host "         alpine sh -c 'tar xzf /src/$(Split-Path -Leaf $outFile) -C /dst'"
    Write-Host '    4. docker compose up -d'
} else {
    Write-Error '输出文件未生成，备份失败'
    exit 1
}
