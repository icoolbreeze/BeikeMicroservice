$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$serviceDir = Join-Path $repoRoot 'services\store_media'
$python = 'C:\ProgramData\miniconda3\python.exe'
$port = 8010

# 清水房本地日 loop:打开后,uvicorn 启动时会顺带起 `roughcast-daily-loop`
# 线程——它内部按 Asia/Shanghai 当日把 A → Shadow score → B 串起来。
# 不要写到仓库的 .env.example(那里留默认 0,本地长跑用这个脚本设进程级 env)。
$env:SM_ROUGHCAST_CRAWL_ENABLED = '1'

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

Write-Output "store_media start requested on 0.0.0.0:$port (roughcast-daily-loop enabled)"
