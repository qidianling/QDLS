' QDLS launcher: one hidden process for the panel, one app-mode window for the UI,
' plus a tray icon so closing the window keeps the console running in the background.
Option Explicit

Dim fso, sh, kitRoot, py, edge, i, candidates
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

kitRoot = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = kitRoot

' python: settings.json first, then a bundled runtime, then PATH
py = ""
Dim cfgPath, txt, re, m
cfgPath = kitRoot & "\settings.json"
If fso.FileExists(cfgPath) Then
  txt = fso.OpenTextFile(cfgPath, 1).ReadAll
  Set re = New RegExp
  re.Pattern = """python""\s*:\s*""([^""]+)"""
  Set m = re.Execute(txt)
  If m.Count > 0 Then py = Replace(m(0).SubMatches(0), "/", "\")
End If
If py = "" Or Not fso.FileExists(py) Then
  If fso.FileExists(kitRoot & "\runtime\python.exe") Then
    py = kitRoot & "\runtime\python.exe"
  Else
    py = "python"
  End If
End If

sh.Run """" & py & """ """ & kitRoot & "\qdls\panel.py""", 0, False
WScript.Sleep 1800

' preferred: Edge app mode (frameless single window)
candidates = Array( _
  sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Microsoft\Edge\Application\msedge.exe", _
  sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\Microsoft\Edge\Application\msedge.exe", _
  sh.ExpandEnvironmentStrings("%LocalAppData%") & "\Microsoft\Edge\Application\msedge.exe", _
  sh.ExpandEnvironmentStrings("%ProgramFiles%") & "\Google\Chrome\Application\chrome.exe", _
  sh.ExpandEnvironmentStrings("%ProgramFiles(x86)%") & "\Google\Chrome\Application\chrome.exe")
edge = ""
For i = 0 To UBound(candidates)
  If edge = "" And fso.FileExists(candidates(i)) Then edge = candidates(i)
Next

If edge <> "" Then
  sh.Run """" & edge & """ --app=http://127.0.0.1:6768/?v=3 --window-size=1120,880", 1, False
Else
  sh.Run "http://127.0.0.1:6768/?v=3", 1, False
End If

' tray helper: closing the window only hides the console, it keeps running here
Dim trayCmd
trayCmd = "powershell -NoProfile -STA -WindowStyle Hidden -ExecutionPolicy Bypass -File """ & kitRoot & "\tools\tray.ps1"" -Kit """ & kitRoot & """"
sh.Run trayCmd, 0, False
