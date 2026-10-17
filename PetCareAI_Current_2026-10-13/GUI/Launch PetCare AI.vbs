' Double-click to open PetCare AI without a terminal window.
Option Explicit
Dim fileSystem, shell, projectFolder, pythonPath, mainPath, command
Set fileSystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
projectFolder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
pythonPath = fileSystem.BuildPath(projectFolder, ".venv-local\Scripts\python.exe")
mainPath = fileSystem.BuildPath(projectFolder, "main.py")

If Not fileSystem.FileExists(pythonPath) Then
    MsgBox "The Python environment is missing. Install the requirements using the README instructions first.", vbExclamation, "PetCare AI"
    WScript.Quit 1
End If
If Not fileSystem.FileExists(mainPath) Then
    MsgBox "main.py is missing. Keep this launcher in the PetCare AI project folder.", vbExclamation, "PetCare AI"
    WScript.Quit 1
End If

shell.CurrentDirectory = projectFolder
command = Chr(34) & pythonPath & Chr(34) & " " & Chr(34) & mainPath & Chr(34)
shell.Run command, 0, False


