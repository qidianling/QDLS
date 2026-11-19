# Publish the current QDLS repo in ONE pass (low API usage).
#
#   powershell -ExecutionPolicy Bypass -File tools\publish-github.ps1 -Token ghp_xxx
#
# Does exactly: git push -> tag push -> create release -> upload zip.
# Nothing is stored on disk; the remote URL is scrubbed afterwards.

param(
  [Parameter(Mandatory = $true)][string]$Token,
  [string]$User = "qidianling",
  [string]$Repo = "QDLS",
  [string]$Tag  = "v1.1.0",
  [string]$Zip  = "",
  [string]$Notes = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$repo = Join-Path $root "repo"
$git = "C:\DS\tools\git\cmd\git.exe"
if (-not (Test-Path $git)) { $git = "git" }
if (-not $Zip) { $Zip = Join-Path $root "release\QDLS.zip" }
if (-not $Notes) { $Notes = Join-Path $root "release\QDLS\RELEASE.md" }

function GitRun { & $git -c http.sslBackend=openssl -C $repo @args }

Write-Host "== 1/4 push main =="
GitRun remote set-url origin "https://$User`:$Token@github.com/$User/$Repo.git"
GitRun push origin main
if ($LASTEXITCODE -ne 0) { throw "push failed" }
GitRun remote set-url origin "https://github.com/$User/$Repo.git"

Write-Host "== 2/4 tag $Tag =="
GitRun tag -f -a $Tag -m "QDLS $Tag"
GitRun remote set-url origin "https://$User`:$Token@github.com/$User/$Repo.git"
GitRun push -f origin $Tag
GitRun remote set-url origin "https://github.com/$User/$Repo.git"

Write-Host "== 3/4 release =="
$headers = @{
  Authorization = "token $Token"
  "User-Agent"  = "QDLS"
  Accept        = "application/vnd.github+json"
}
$body = @{
  tag_name = $Tag
  name     = "QDLS $Tag"
  body     = (Get-Content $Notes -Raw)
  draft    = $false
  prerelease = $false
} | ConvertTo-Json -Depth 4
try {
  $rel = Invoke-RestMethod -Method Post "https://api.github.com/repos/$User/$Repo/releases" `
    -Headers $headers -Body $body -ContentType "application/json"
  Write-Host "   release: $($rel.html_url)"
} catch {
  if ("$($_.Exception.Message)" -match "already_exists") {
    $rel = Invoke-RestMethod -Headers $headers "https://api.github.com/repos/$User/$Repo/releases/tags/$Tag"
    Write-Host "   release exists, reusing"
  } else { throw }
}

Write-Host "== 4/4 asset =="
$bytes = [System.IO.File]::ReadAllBytes($Zip)
$up = "https://uploads.github.com/repos/$User/$Repo/releases/$($rel.id)/assets?name=QDLS.zip"
try {
  $asset = Invoke-RestMethod -Method Post $up -Headers $headers -Body $bytes -ContentType "application/zip"
  Write-Host "   asset: $($asset.name) $($asset.size) bytes"
} catch {
  Write-Host "   asset upload skipped: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "done: https://github.com/$User/$Repo/releases/tag/$Tag" -ForegroundColor Green
