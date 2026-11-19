# Give the QDLS app window a proper big icon (WM_SETICON) so the taskbar
# button is not a scaled-down 32 px bitmap.
param(
  [string]$Title = "QDLS",
  [string]$Icon  = ""
)

$ErrorActionPreference = "SilentlyContinue"
Add-Type -AssemblyName System.Drawing

if (-not $Icon) {
  $root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
  $Icon = Join-Path $root "qdls\assets\icon.ico"
}
if (-not (Test-Path $Icon)) { exit 0 }

Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class WinIcon {
    public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern IntPtr SendMessageW(IntPtr h, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr LoadImageW(IntPtr inst, string name, uint type, int cx, int cy, uint flags);

    public static List<IntPtr> ByTitle(string title) {
        var found = new List<IntPtr>();
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            var sb = new StringBuilder(512);
            GetWindowTextW(h, sb, sb.Capacity);
            var t = sb.ToString().Trim();
            if (t == title || t.StartsWith(title)) found.Add(h);
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static void Set(IntPtr h, string path, int small, int big) {
        IntPtr iSmall = LoadImageW(IntPtr.Zero, path, 1, small, small, 0x10);
        IntPtr iBig   = LoadImageW(IntPtr.Zero, path, 1, big, big, 0x10);
        if (iSmall != IntPtr.Zero) SendMessageW(h, 0x0080, IntPtr.Zero, iSmall);
        if (iBig   != IntPtr.Zero) SendMessageW(h, 0x0080, new IntPtr(1), iBig);
    }
}
"@

for ($i = 0; $i -lt 120; $i++) {
  $wins = [WinIcon]::ByTitle($Title)
  if ($wins.Count -gt 0) {
    for ($round = 0; $round -lt 3; $round++) {
      foreach ($h in $wins) {
        [WinIcon]::Set($h, $Icon, 32, 64)
        [WinIcon]::Set($h, $Icon, 32, 48)
      }
      Start-Sleep -Seconds 2
    }
    exit 0
  }
  Start-Sleep -Seconds 1
}
exit 0
