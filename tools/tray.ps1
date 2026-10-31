# QDLS tray helper: keeps the console one click away after the window is closed.
# Started hidden by launch.vbs. Runs as a single instance.

param(
  [string]$Kit = "",
  [int]$Port = 6768
)

$ErrorActionPreference = "SilentlyContinue"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

if (-not $Kit) { $Kit = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$iconPath = Join-Path $Kit "qdls\assets\icon.ico"
if (-not (Test-Path $iconPath)) { $iconPath = Join-Path $Kit "icon.ico" }

# --- single instance
$created = $false
$mutex = New-Object System.Threading.Mutex($true, "Global\QDLS.tray", [ref]$created)
if (-not $created) { exit 0 }

function Open-Console {
  $edge = @(
    "$env:ProgramFiles(x86)\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:LocalAppData\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles(x86)\Google\Chrome\Application\chrome.exe"
  ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
  $url = "http://127.0.0.1:$Port/"
  if ($edge) {
    Start-Process -FilePath $edge -ArgumentList "--app=$url", "--window-size=1120,880"
  } else {
    Start-Process $url
  }
}

function Close-ConsoleWindow {
  # the app-mode browser window runs in its own process; close it too
  try {
    $procs = Get-CimInstance Win32_Process -Filter "Name='msedge.exe' OR Name='chrome.exe'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
      if ($p.CommandLine -and $p.CommandLine -like "*--app=http://127.0.0.1:$Port*") {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
      }
    }
  } catch {
    try {
      Get-Process msedge, chrome -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowTitle -eq 'QDLS' } |
        ForEach-Object { $_.CloseMainWindow() | Out-Null }
    } catch {}
  }
}

function Stop-QDLS {
  Close-ConsoleWindow
  try { Invoke-RestMethod -Method Post "http://127.0.0.1:$Port/api/stop" -TimeoutSec 10 | Out-Null } catch {}
  Start-Sleep -Milliseconds 1200
  try {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -First 1
    if ($listener) { Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue }
  } catch {}
  [System.Windows.Forms.Application]::Exit()
}

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$open = $menu.Items.Add("打开控制台")
$open.add_Click({ Open-Console })
$folder = $menu.Items.Add("打开工作目录")
$folder.add_Click({ Start-Process (Join-Path $Kit "work") })
$menu.Items.Add("-") | Out-Null
$restart = $menu.Items.Add("重启网关")
$restart.add_Click({ try { Invoke-RestMethod -Method Post "http://127.0.0.1:$Port/api/restart" -TimeoutSec 60 | Out-Null } catch {} })
$menu.Items.Add("-") | Out-Null
$quit = $menu.Items.Add("退出 QDLS（停止服务）")
$quit.add_Click({ Stop-QDLS })

$tray = New-Object System.Windows.Forms.NotifyIcon
$tray.Icon = if (Test-Path $iconPath) { New-Object System.Drawing.Icon($iconPath) } else { [System.Drawing.SystemIcons]::Application }
$tray.Text = "QDLS 控制台（双击打开）"
$tray.ContextMenuStrip = $menu
$tray.Visible = $true
$tray.add_DoubleClick({ Open-Console })
$tray.add_MouseClick({ if ($_.Button -eq [System.Windows.Forms.MouseButtons]::Left) { } })

# balloon on first run only
$flag = Join-Path $env:LOCALAPPDATA "QDLS\tray-shown"
if (-not (Test-Path $flag)) {
  New-Item -ItemType Directory -Force (Split-Path $flag) | Out-Null
  New-Item -ItemType File -Force $flag | Out-Null
  $tray.ShowBalloonTip(4000, "QDLS 在后台运行", "关闭窗口不会退出；双击托盘图标可以重新打开控制台。", [System.Windows.Forms.ToolTipIcon]::Info)
}

[System.Windows.Forms.Application]::Run()
$tray.Visible = $false
$tray.Dispose()
