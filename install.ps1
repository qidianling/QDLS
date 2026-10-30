# QDLS installer (Windows 10/11, 64-bit)
#
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# Steps: check python -> install deps -> fetch SoulX-Singer repo -> fetch models
#        -> write settings.json -> put one shortcut on the Desktop.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
Write-Host "== QDLS installer ==" -ForegroundColor Cyan

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# 1) python
$py = $null
foreach ($cand in @("python", "py -3.11", "py -3.10")) {
  try {
    $ver = & cmd /c "$cand -c `"import sys;print(sys.version_info[:2])`"" 2>$null
    if ($LASTEXITCODE -eq 0 -and $ver -match "3\.(10|11)") { $py = $cand; break }
  } catch {}
}
if (-not $py) {
  Fail "Python 3.10/3.11 (64-bit) not found. Install it from python.org and re-run."
}
Write-Host "python: $py" -ForegroundColor Green

# 2) dependencies
Write-Host "`n[1/5] installing python packages (torch is ~2.5 GB) ..."
& cmd /c "$py -m pip install --upgrade pip"
& cmd /c "$py -m pip install -r requirements.txt"
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }

# 3) engine repo (SoulX-Singer)
if (-not (Test-Path "$root\SoulX-Singer")) {
  Write-Host "`n[2/5] cloning SoulX-Singer ..."
  & git clone --depth 1 https://github.com/Soul-AILab/SoulX-Singer.git "$root\SoulX-Singer"
  if ($LASTEXITCODE -ne 0) {
    Write-Host "git clone failed - download the repo zip manually into .\SoulX-Singer" -ForegroundColor Yellow
  }
} else {
  Write-Host "`n[2/5] SoulX-Singer already present"
}

# 4) models
Write-Host "`n[3/5] fetching checkpoints (about 3 GB) ..."
& cmd /c "$py tools\fetch_models.py"
if ($LASTEXITCODE -ne 0) {
  Write-Host "model download incomplete - see messages above" -ForegroundColor Yellow
}

# 5) settings
Write-Host "`n[4/5] writing settings.json ..."
if (-not (Test-Path "$root\settings.json")) {
  $ex = Get-Content "$root\settings.example.json" -Raw | ConvertFrom-Json
  $ex.paths.repo = "$root\SoulX-Singer"
  $ex.paths.pretrained = "$root\pretrained"
  $ex.paths.voices = "./voices"
  $ex.paths.work = "./work"
  $ex.paths.python = "$py"
  $ex | ConvertTo-Json -Depth 8 | Set-Content "$root\settings.json" -Encoding UTF8
}

# 6) desktop shortcut (single entry point)
Write-Host "`n[5/5] creating desktop shortcut ..."
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "QDLS.lnk"
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = "wscript.exe"
$sc.Arguments = "`"$root\launch.vbs`""
$sc.WorkingDirectory = $root
# Explorer cannot read icons inside low-integrity folders, so park a copy in the profile
$iconDir = Join-Path $env:LOCALAPPDATA "QDLS"
New-Item -ItemType Directory -Force $iconDir | Out-Null
$srcIco = Join-Path $root "qdls\assets\icon_bmp.ico"
if (-not (Test-Path $srcIco)) { $srcIco = Join-Path $root "qdls\assets\icon.ico" }
$ico = Join-Path $iconDir "icon.ico"
if (Test-Path $srcIco) { Copy-Item $srcIco $ico -Force }
if (Test-Path $ico) { $sc.IconLocation = "$ico,0" }
$sc.Description = "QDLS - singing voice conversion"
$sc.Save()

Write-Host "`nDone. Double-click QDLS on the Desktop." -ForegroundColor Green
Write-Host "First launch loads the model (~30 s) and opens the console window."
Write-Host "Hardware: >=8 GB VRAM recommended, >=16 GB RAM, 12 GB free disk."
