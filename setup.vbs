' Start the QDLS setup wizard hidden and open its download page.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = root

py = ""
cfg = root & "\settings.json"
If fso.FileExists(cfg) Then
  txt = fso.OpenTextFile(cfg, 1).ReadAll
  Set re = New RegExp
  re.Pattern = """python""\s*:\s*""([^""]+)"""
  Set m = re.Execute(txt)
  If m.Count > 0 Then py = Replace(m(0).SubMatches(0), "/", "\")
End If
If py = "" Or Not fso.FileExists(py) Then
  If fso.FileExists(root & "\runtime\python.exe") Then
    py = root & "\runtime\python.exe"
  Else
    py = "python"
  End If
End If

sh.Run """" & py & """ """ & root & "\setup.py""", 0, False
WScript.Sleep 1500
sh.Run "http://127.0.0.1:6790/", 1, False
