$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$serviceDir = Join-Path $repoRoot 'services\crm_connector'
$python = 'C:\ProgramData\miniconda3\python.exe'
$port = 8020
$yunshuExe = 'C:\Program Files\YunShu\YunShu.exe'
$yunshuLauncher = 'C:\Program Files\YunShu\YunShuLauncher.exe'

function Ensure-YunShu {
    $running = Get-Process -Name 'YunShu' -ErrorAction SilentlyContinue
    if ($running) {
        Write-Output 'YunShu is already running'
        return
    }
    $exe = if (Test-Path -LiteralPath $yunshuLauncher) { $yunshuLauncher } else { $yunshuExe }
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "YunShu was not found at $yunshuExe"
    }
    Write-Output "Starting YunShu: $exe"
    Start-Process -FilePath $exe
    $deadline = (Get-Date).AddSeconds(45)
    do {
        Start-Sleep -Seconds 1
        $running = Get-Process -Name 'YunShu' -ErrorAction SilentlyContinue
    } while (-not $running -and (Get-Date) -lt $deadline)
    if (-not $running) {
        throw 'YunShu did not start within 45 seconds'
    }
    Write-Output 'YunShu process is up'
}

Ensure-YunShu

# 精选大屏快照定时推送：由连接器内置守护线程负责（每小时采集并 scp 到云端），
# 不再依赖 Windows 计划任务。
$env:CC_FEATURED_PUSH_ENABLED = '1'
$env:CC_FEATURED_PUSH_INTERVAL_SECONDS = '3600'
$env:CC_FEATURED_PUSH_LOCAL_API_BASE_URL = 'http://127.0.0.1:8020'
$env:CC_FEATURED_PUSH_REMOTE_HOST = 'beike-server'
$env:CC_FEATURED_PUSH_REMOTE_PATH = '/var/lib/store-media/featured_snapshot.json'

# 租赁房源本地化默认开启（按行政区轮询全部房源，已本地化的不打详情）。
$env:CC_RENTAL_ARCHIVE_ENABLED = '1'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python runtime was not found: $python"
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
if ($listener) {
    Write-Output "crm_connector is already listening on 127.0.0.1:$port"
    exit 0
}

New-Item -ItemType Directory -Force -Path (Join-Path $serviceDir 'run') | Out-Null
Start-Process `
    -FilePath $python `
    -ArgumentList '-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', '127.0.0.1', '--port', "$port" `
    -WorkingDirectory $serviceDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $serviceDir 'run\crm_connector.out.log') `
    -RedirectStandardError (Join-Path $serviceDir 'run\crm_connector.err.log')

Write-Output "crm_connector start requested on 127.0.0.1:$port"
