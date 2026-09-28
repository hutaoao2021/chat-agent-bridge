#ifndef BundleDir
  #error BundleDir required
#endif
#ifndef OutputDir
  #error OutputDir required
#endif
[Setup]
AppId=ChatAgentBridge.Desktop
AppName=Chat Agent Bridge
AppVersion=0.2.1
DefaultDirName={localappdata}\ChatAgentBridge\app
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=ChatAgentBridge-Setup-0.2.1-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayName=Chat Agent Bridge
[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{userdesktop}\Chat Agent Bridge"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m chat_agent_bridge.desktop --app-dir ""{app}"""; WorkingDir: "{app}"
Name: "{userprograms}\Chat Agent Bridge"; Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m chat_agent_bridge.desktop --app-dir ""{app}"""; WorkingDir: "{app}"
[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m chat_agent_bridge.desktop --app-dir ""{app}"""; Description: "Open Chat Agent Bridge"; Flags: postinstall nowait skipifsilent
[Code]
function StopInstalled(DisableStartup: Boolean): Boolean;
var ExitCode: Integer; Args, Interpreter: String;
begin
  Result := True;
  Interpreter := ExpandConstant('{app}\runtime\python.exe');
  if not FileExists(Interpreter) then exit;
  Args := '-m chat_agent_bridge.desktop --app-dir "' + ExpandConstant('{app}') + '" --maintenance';
  if DisableStartup then Args := Args + ' --disable';
  Result := Exec(Interpreter, Args, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode);
  Result := Result and (ExitCode = 0);
end;
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not StopInstalled(False) then Result := 'Service shutdown or absence of running jobs could not be confirmed. Check tasks and diagnostics before retrying.';
end;
function InitializeUninstall(): Boolean;
begin
  Result := StopInstalled(True);
  if not Result then MsgBox('Services or jobs may still be running; uninstall cancelled. Check tasks and diagnostics before retrying.', mbError, MB_OK);
end;
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var DataPath: String;
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then begin
    if MsgBox('Keep saved settings and task history? Yes keeps data. No deletes only this app''s data; project directories are preserved.', mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDNO then begin
      DataPath := ExpandConstant('{localappdata}\ChatAgentBridge\data');
      { Fixed per-user path only; no configurable workspace or user-selected path is deleted. }
      if DirExists(DataPath) then DelTree(DataPath, True, True, True);
    end;
  end;
end;
