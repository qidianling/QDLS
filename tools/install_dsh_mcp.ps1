# Prepare the QDLS <-> DSH link on this machine.
#
#   powershell -ExecutionPolicy Bypass -File tools\install_dsh_mcp.ps1
#
# It only writes local files:
#   1. dsh-bundle\cordis.patch.yml  (absolute python + script paths for THIS box)
#   2. qdls.token                  (API token, if one does not exist yet)
# It only talks to the local QDLS console.

param(
  [string]$Api = "http://127.0.0.1:6768"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# --- python: settings.json -> runtime -> PATH
$py = ""
$cfg = Join-Path $root "settings.json"
if (Test-Path $cfg) { try { $py = (Get-Content $cfg -Raw | ConvertFrom-Json).paths.python } catch {} }
if (-not $py -or -not (Test-Path $py)) {
  $rt = Join-Path $root "runtime\python.exe"
  if (Test-Path $rt) { $py = $rt }
}
if (-not $py -or -not (Test-Path $py)) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $py = $cmd.Source }
}
if (-not $py -or -not (Test-Path $py)) {
  Write-Host "找不到 python：请先运行 QDLS 安装向导（setup.vbs）" -ForegroundColor Red
  exit 1
}
$py = $py.Replace('\', '/')
$script = (Join-Path $root "qdls_mcp.py").Replace('\', '/')
$cwd = $root.Replace('\', '/')

# --- token: ask the local console, which also records it in the token list
$tokenFile = Join-Path $root "qdls.token"
$haveToken = $false
if (Test-Path $tokenFile) {
  $haveToken = ((Get-Content $tokenFile -Raw).Trim().Length -gt 10)
}
if (-not $haveToken) {
  try {
    $r = Invoke-RestMethod -Method Post "$Api/api/access/dsh_credential" -ContentType "application/json" -Body "{}" -TimeoutSec 15
    if ($r.ok) { Write-Host "已生成凭据：$($r.path)" -ForegroundColor Green }
  } catch {
    Write-Host "QDLS 控制台没运行，先打开 QDLS 再重跑本脚本。" -ForegroundColor Yellow
    Write-Host "（也可以手动：控制台 -> 接入 -> 写入 DSH 凭据）"
  }
}

# --- bundle config with this machine's absolute paths
$yaml = @"
- insert:
    - id: qdls-mcp
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: qdls
        transport: stdio
        command: '$py'
        args:
          - '$script'
        env:
          QDLS_URL: '$Api'
          PYTHONUTF8: '1'
          PYTHONIOENCODING: 'utf-8'
        cwd: '$cwd'
        failOnStartupError: true
"@
$out = Join-Path $root "dsh-bundle\cordis.patch.yml"
New-Item -ItemType Directory -Force (Split-Path $out) | Out-Null
$yaml | Set-Content $out -Encoding UTF8
Write-Host "已写入 $out" -ForegroundColor Green
Write-Host ""
Write-Host "接下来："
Write-Host "  1. QDLS 控制台「接入」点一下「写入 DSH 凭据」（如果还没生成）"
Write-Host "  2. DSH -> 插件管理 -> 安装 bundle： $root\dsh-bundle"
Write-Host "  3. 重启 DSH，新会话应出现 mcp__qdls__* 工具"
Write-Host ""
Write-Host "自检： powershell -ExecutionPolicy Bypass -File $root\tools\test_mcp.ps1"
