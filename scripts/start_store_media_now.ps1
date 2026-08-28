$ErrorActionPreference = 'Stop'
$serviceDir = 'C:\Users\shipi\vswp\BeikeMicroservice\services\store_media'
$python = 'C:\ProgramData\miniconda3\python.exe'
$port = 8010

# 显式关掉清水房 daily loop,避免无授权打到真实 CRM 上游
$env:SM_ROUGHCAST_CRAWL_ENABLED = '0'
$env:SM_BOOTSTRAP_ADMIN_USERNAME = ''
$env:SM_BOOTSTRAP_ADMIN_PASSWORD = ''

$listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
if ($listener) {
    Write-Output "store_media is already listening on 127.0.0.1:$port"
    exit 0
}

New-Item -ItemType Directory -Force -Path (Join-Path $serviceDir 'run') | Out-Null
Set-Location $serviceDir
Start-Process `
    -FilePath $python `
    -ArgumentList '-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', '0.0.0.0', '--port', "$port" `
    -WindowStyle Hidden `
    -RedirectStandardOutput '.\run\store_media.out.log' `
    -RedirectStandardError '.\run\store_media.err.log'
Write-Output "store_media start requested on 0.0.0.0:$port (daily loop OFF)"
