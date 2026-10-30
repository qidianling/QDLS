# Enable WSL2 for the QDLS AMD (ROCm) path.  Run as Administrator.
$ErrorActionPreference = "Continue"

Write-Host "== QDLS / AMD: WSL2 准备 ==" -ForegroundColor Cyan

$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) {
  Write-Host "未找到 wsl.exe，请先升级到 Windows 10 21H2+ 或 Windows 11" -ForegroundColor Red
  exit 1
}

$virt = (Get-CimInstance Win32_Processor).VirtualizationFirmwareEnabled
if ($virt -eq $false) {
  Write-Host "BIOS 里没有开启虚拟化（VT-x/SVM），请先在 BIOS 打开 SVM Mode" -ForegroundColor Red
  exit 1
}

Write-Host "[1/3] 启用 WSL 与虚拟机平台 ..."
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart | Out-Null
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart | Out-Null

Write-Host "[2/3] 安装 Ubuntu 22.04 ..."
wsl --install -d Ubuntu-22.04

Write-Host "[3/3] 检查结果"
wsl -l -v

Write-Host ""
Write-Host "如果刚刚启用了功能，请重启电脑，然后进入 Ubuntu：" -ForegroundColor Yellow
Write-Host "  wsl -d Ubuntu-22.04"
Write-Host "接着执行 QDLS\tools\amd\setup_rocm.sh（在 WSL 内）" -ForegroundColor Yellow
