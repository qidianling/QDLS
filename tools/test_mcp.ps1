# Standalone check for the QDLS MCP server (no DSH needed).
#   powershell -ExecutionPolicy Bypass -File tools\test_mcp.ps1
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$py = ""
$cfg = Join-Path $root "settings.json"
if (Test-Path $cfg) { try { $py = (Get-Content $cfg -Raw | ConvertFrom-Json).paths.python } catch {} }
if (-not $py -or -not (Test-Path $py)) { $py = Join-Path $root "runtime\python.exe" }
if (-not (Test-Path $py)) { $py = "python" }

$token = ""
$tf = Join-Path $root "qdls.token"
if (Test-Path $tf) { $token = (Get-Content $tf -Raw).Trim() }
$patch = Join-Path $root "dsh-bundle\cordis.patch.yml"
if (-not $token -and (Test-Path $patch)) {
  $m = Select-String -Path $patch -Pattern "QDLS_TOKEN:\s*'([^']+)'" | Select-Object -First 1
  if ($m) { $token = $m.Matches[0].Groups[1].Value }
}

Write-Host "python : $py"
Write-Host "script : $root\qdls_mcp.py"
Write-Host "token  : $($token.Substring(0,[Math]::Min(12,$token.Length)))..."

$env:QDLS_URL = "http://127.0.0.1:6768"
$env:QDLS_TOKEN = $token
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
$list = '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
$call = '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"qdls_status","arguments":{}}}'

$input = @($init, $list, $call) -join "`n"
$out = $input | & $py "$root\qdls_mcp.py" 2>$null

$ok = $true
foreach ($line in $out) {
  try { $d = $line | ConvertFrom-Json } catch { continue }
  switch ($d.id) {
    1 { Write-Host "initialize : OK ($($d.result.serverInfo.name) $($d.result.serverInfo.version))" -ForegroundColor Green }
    2 { $names = ($d.result.tools | ForEach-Object { $_.name }) -join ", "
        Write-Host "tools/list : $names" -ForegroundColor Green }
    3 { if ($d.result.isError) { Write-Host "tools/call : FAILED - $($d.result.content[0].text)" -ForegroundColor Red; $ok = $false }
        else { Write-Host "tools/call : $($d.result.content[0].text)" -ForegroundColor Green } }
  }
}
if (-not $out) { Write-Host "没有任何输出：MCP 进程启动失败（检查 python 路径）" -ForegroundColor Red; exit 1 }
if (-not $ok) { exit 1 }
Write-Host "`nMCP 服务器本身正常；如果 DSH 里仍报错，把 DSH 的插件错误文本发出来。" -ForegroundColor Cyan
