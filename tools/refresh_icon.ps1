# Rebuild the Windows icon cache and refresh the QDLS shortcut icon.
# Run this only if the desktop shortcut still shows a generic icon:
#   powershell -ExecutionPolicy Bypass -File tools\refresh_icon.ps1
$ErrorActionPreference = "SilentlyContinue"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ico = Join-Path $root "qdls\assets\qdls_v2.ico"
if (-not (Test-Path $ico)) { $ico = Join-Path $root "qdls\assets\icon.ico" }

Write-Host "icon: $ico"

# recreate the shortcut so Explorer re-reads it
$desk = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desk "QDLS.lnk"
Remove-Item $lnk -Force
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = "wscript.exe"
$sc.Arguments = "`"$root\launch.vbs`""
$sc.WorkingDirectory = $root
$sc.IconLocation = "$ico,0"
$sc.Description = "QDLS - local singing voice conversion"
$sc.Save()
Write-Host "shortcut recreated"

# clear cached thumbnails/icons and restart Explorer
$cachePaths = @(
  "$env:LOCALAPPDATA\IconCache.db",
  "$env:LOCALAPPDATA\Microsoft\Windows\Explorer\iconcache_*.db",
  "$env:LOCALAPPDATA\Microsoft\Windows\Explorer\thumbcache_*.db"
)
Taskkill /IM explorer.exe /F | Out-Null
foreach ($p in $cachePaths) {
  Get-ChildItem $p -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
  }
}
Start-Process explorer.exe
Start-Sleep -Seconds 2
Write-Host "done - the desktop should now show the QDLS icon" -ForegroundColor Green
