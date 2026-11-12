$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$serviceDir = Join-Path $repoRoot 'services\store_media'
$python = 'C:\ProgramData\miniconda3\python.exe'
$port = 8010

# 清水房 daily loop 默认关闭，避免和租赁本地化抢同一 CRM 账号额度。
# 需要采集时再改为 1。
$env:SM_ROUGHCAST_CRAWL_ENABLED = '0'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python runtime was not found: $python"
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
if ($listener) {
    Write-Output "store_media is already listening on 127.0.0.1:$port"
    exit 0
}

New-Item -ItemType Directory -Force -Path (Join-Path $serviceDir 'run') | Out-Null
Start-Process `
    -FilePath $python `
    -ArgumentList '-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', '0.0.0.0', '--port', "$port" `
    -WorkingDirectory $serviceDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $serviceDir 'run\store_media.out.log') `
    -RedirectStandardError (Join-Path $serviceDir 'run\store_media.err.log')

Write-Output "store_media start requested on 0.0.0.0:$port (roughcast-daily-loop disabled)"
