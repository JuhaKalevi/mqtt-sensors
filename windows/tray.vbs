Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(here)
tray = fso.BuildPath(here, "tray.py")
pythonw = ""
candidates = Array( _
    fso.BuildPath(fso.BuildPath(root, "venv\Scripts"), "pythonw.exe"), _
    fso.BuildPath(fso.BuildPath(root, ".venv\Scripts"), "pythonw.exe"))
For i = 0 To UBound(candidates)
    If fso.FileExists(candidates(i)) Then
        pythonw = candidates(i)
        Exit For
    End If
Next
If pythonw = "" Then
    pathText = shell.Environment("PROCESS")("PATH")
    parts = Split(pathText, ";")
    For Each folder In parts
        folder = Trim(folder)
        If folder <> "" Then
            candidate = fso.BuildPath(folder, "pythonw.exe")
            If fso.FileExists(candidate) Then
                pythonw = candidate
                Exit For
            End If
        End If
    Next
End If
If pythonw = "" Then
    MsgBox "pythonw.exe was not found in venv, .venv, or PATH.", vbCritical, "mqtt-sensors"
    WScript.Quit 1
End If
shell.Run """" & pythonw & """ """ & tray & """", 0, False
