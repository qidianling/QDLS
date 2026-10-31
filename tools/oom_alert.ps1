# Topmost notice shown by the panel before it stops itself on OOM risk.
# Usage: powershell -File tools\oom_alert.ps1 -Message "..." [-Seconds 15]

param(
  [string]$Message = "QDLS 即将因内存不足而停止。",
  [int]$Seconds = 15
)

$ErrorActionPreference = "SilentlyContinue"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = "QDLS 内存保护"
$form.Width = 520
$form.Height = 260
$form.StartPosition = "CenterScreen"
$form.TopMost = $true
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.BackColor = [System.Drawing.Color]::FromArgb(247, 248, 251)

$icon = New-Object System.Windows.Forms.PictureBox
$icon.Size = New-Object System.Drawing.Size(48, 48)
$icon.Location = New-Object System.Drawing.Point(24, 26)
$icon.SizeMode = "StretchImage"
$ico = Join-Path (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)) "qdls\assets\icon.ico"
if (Test-Path $ico) {
  $icon.Image = (New-Object System.Drawing.Icon($ico, 48, 48)).ToBitmap()
} else {
  $icon.Image = [System.Drawing.SystemIcons]::Warning.ToBitmap()
}
$form.Controls.Add($icon)

$title = New-Object System.Windows.Forms.Label
$title.Text = "内存占用过高，正在停止 QDLS"
$title.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 12, [System.Drawing.FontStyle]::Bold)
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(86, 26)
$form.Controls.Add($title)

$body = New-Object System.Windows.Forms.Label
$body.Text = $Message
$body.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 9)
$body.AutoSize = $false
$body.Size = New-Object System.Drawing.Size(440, 96)
$body.Location = New-Object System.Drawing.Point(86, 62)
$form.Controls.Add($body)

$ok = New-Object System.Windows.Forms.Button
$ok.Text = "知道了，立即停止"
$ok.Size = New-Object System.Drawing.Size(150, 32)
$ok.Location = New-Object System.Drawing.Point(340, 170)
$ok.Add_Click({ $form.Close() })
$form.Controls.Add($ok)

$countdown = New-Object System.Windows.Forms.Label
$countdown.Text = "$Seconds 秒后自动关闭"
$countdown.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 8)
$countdown.ForeColor = [System.Drawing.Color]::Gray
$countdown.AutoSize = $true
$countdown.Location = New-Object System.Drawing.Point(24, 178)
$form.Controls.Add($countdown)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 1000
$left = $Seconds
$timer.Add_Tick({
  $left--
  $countdown.Text = "$left 秒后自动关闭"
  if ($left -le 0) {
    $timer.Stop()
    $form.Close()
  }
})
$timer.Start()

[void]$form.ShowDialog()
