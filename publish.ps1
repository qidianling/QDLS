# Publish QDLS to GitHub
#
#   powershell -ExecutionPolicy Bypass -File publish.ps1 -Repo QDLS
#
# Installs git/gh with winget when missing, initialises the repo, commits and
# (after `gh auth login`) creates the GitHub repository and pushes it.

param(
  [string]$Repo = "QDLS",
  [string]$Visibility = "public",
  [string]$Account = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

if (-not (Have git)) {
  Write-Host "git not found, installing with winget ..." -ForegroundColor Yellow
  winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
  $env:Path += ";C:\Program Files\Git\cmd"
}
if (-not (Have gh)) {
  Write-Host "GitHub CLI not found, installing with winget ..." -ForegroundColor Yellow
  winget install --id GitHub.cli -e --silent --accept-package-agreements --accept-source-agreements
  $env:Path += ";C:\Program Files\GitHub CLI"
  $env:Path += ";$env:LOCALAPPDATA\GitHubCLI\bin"
}
if (-not (Have git)) { throw "git still missing, install Git for Windows manually" }

if (-not (Test-Path ".git")) {
  git init -b main
}
git add -A
if ((git status --porcelain).Length -gt 0) {
  git commit -m "QDLS: local zero-shot singing voice conversion workbench"
}

if (-not (Have gh)) {
  Write-Host "`nRepo is committed locally: $root" -ForegroundColor Green
  Write-Host "Install GitHub CLI, run 'gh auth login', then:"
  Write-Host "  gh repo create $Repo --$Visibility --source . --push"
  exit 0
}

$auth = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host "`n需要登录 GitHub（浏览器授权一次）" -ForegroundColor Yellow
  gh auth login --web --git-protocol https
}

gh repo create $Repo --$Visibility --source . --remote origin --push
if ($LASTEXITCODE -eq 0) {
  Write-Host "`nPublished: https://github.com/$((gh api user --jq .login))/$Repo" -ForegroundColor Green
  Write-Host "Create the release notes from RELEASE.md, and upload the zip from release/QDLS.zip"
} else {
  Write-Host "push failed - you can also create the repo on github.com and run:" -ForegroundColor Yellow
  Write-Host "  git remote add origin https://github.com/<user>/$Repo.git"
  Write-Host "  git push -u origin main"
}
