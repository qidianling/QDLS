# Publish the QDLS repository to GitHub.
#
#   powershell -ExecutionPolicy Bypass -File tools\publish-github.ps1 -User <github-user> -Token <PAT>
#
# -Token needs a classic PAT with "repo" scope (or fine-grained: Contents r/w + Administration r/w).
# Nothing is stored on disk: the token is only used for this run.

param(
  [string]$User = "qidianling",
  [Parameter(Mandatory = $true)][string]$Token,
  [string]$Repo = "QDLS",
  [string]$Description = "Local zero-shot singing voice conversion workbench (non-commercial)",
  [switch]$Private
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$git = Join-Path (Split-Path -Parent $repo) "tools\git\cmd\git.exe"
if (-not (Test-Path $git)) { $git = "git" }

# schannel on some machines cannot talk to github; openssl backend can
function GitRun { & $git -c http.sslBackend=openssl -C $repo @args }

Write-Host "== 1/3 检查仓库 =="
if (-not (Test-Path (Join-Path $repo ".git"))) {
  GitRun init -b main | Out-Null
  GitRun config user.name "QDLS"
  GitRun config user.email "qdls@local"
}
GitRun add -A
$status = GitRun status --porcelain
if ($status) { GitRun commit -m "QDLS: update" | Out-Null }
Write-Host "   commit: $(GitRun log --oneline -1)"

Write-Host "== 2/3 创建 GitHub 仓库 =="
$body = @{ name = $Repo; description = $Description; private = [bool]$Private; auto_init = $false } | ConvertTo-Json
$headers = @{ Authorization = "token $Token"; "User-Agent" = "QDLS-publish"; Accept = "application/vnd.github+json" }
try {
  $r = Invoke-RestMethod -Method Post "https://api.github.com/user/repos" -Headers $headers -Body $body -ContentType "application/json" -TimeoutSec 40
  Write-Host "   已创建: $($r.html_url)" -ForegroundColor Green
} catch {
  $msg = $_.Exception.Message
  if ($msg -match "already exists|Name already exists") {
    Write-Host "   仓库已存在，直接推送" -ForegroundColor Yellow
  } else {
    Write-Host "   创建失败：$msg" -ForegroundColor Red
    exit 1
  }
}

Write-Host "== 3/3 推送 =="
$remote = "https://$User`:$Token@github.com/$User/$Repo.git"
GitRun remote remove origin 2>$null | Out-Null
GitRun remote add origin $remote
GitRun push -u origin main --force
if ($LASTEXITCODE -ne 0) { Write-Host "推送失败" -ForegroundColor Red; exit 1 }
# strip the token from .git/config
GitRun remote set-url origin "https://github.com/$User/$Repo.git"

Write-Host ""
Write-Host "完成：https://github.com/$User/$Repo" -ForegroundColor Green
Write-Host "接下来在网页上："
Write-Host "  - Releases → Draft a new release → tag v1.0.0"
Write-Host "  - 正文粘贴 RELEASE.md，附件上传 release/QDLS.zip"

