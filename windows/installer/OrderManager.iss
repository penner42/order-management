#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{45DA6158-181E-4D2A-927B-84AC77D34D89}
AppName=Order Manager
AppVersion={#AppVersion}
DefaultDirName={localappdata}\OrderManager\app
DisableDirPage=yes
DefaultGroupName=Order Manager
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.22000
OutputDir=output
OutputBaseFilename=OrderManager-Setup-x64
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\OrderManager.exe
WizardStyle=modern

[Files]
Source: "launcher\OrderManager.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Order Manager"; Filename: "{app}\OrderManager.exe"
Name: "{autodesktop}\Order Manager"; Filename: "{app}\OrderManager.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\OrderManager.exe"; Description: "Start Order Manager and complete Docker setup"; Flags: nowait postinstall skipifsilent

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "OrderManager"; ValueData: """{app}\OrderManager.exe"" --startup"; Flags: createvalueifdoesntexist uninsdeletevalue

[Code]
function InitializeUninstall(): Boolean;
var
  ExitCode: Integer;
begin
  Result := Exec(ExpandConstant('{app}\OrderManager.exe'), '--uninstall', '', SW_HIDE,
    ewWaitUntilTerminated, ExitCode);
  if Result then Result := ExitCode = 0;
  if not Result then
    MsgBox('Order Manager could not stop its containers. Start Docker Desktop, finish any active update, and retry uninstall. Your data has been retained.', mbError, MB_OK);
end;
