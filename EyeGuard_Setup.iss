; EyeGuard Inno Setup Script
; Author: @hocine_chero

#define MyAppName "EyeGuard"
#define MyAppVersion "3.1.0"
#define MyAppPublisher "@hocine_chero"
#define MyAppExeName "EyeGuard.exe"

[Setup]
AppId={{D37E88BC-89B4-4B2D-A491-17C17483B892}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion=3.1.0.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=EyeGuard Setup
VersionInfoTextVersion=3.1.0
VersionInfoCopyright=Copyright (c) 2026 @hocine_chero. MIT License.
VersionInfoProductName=EyeGuard
VersionInfoProductVersion=3.1.0.0
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist\installer
OutputBaseFilename=EyeGuard_Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no
SetupIconFile=assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupicon"; Description: "Launch {#MyAppName} automatically when Windows starts"; GroupDescription: "Startup:"

[Files]
Source: "dist\EyeGuard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Force kill EyeGuard process immediately before uninstallation begins
Filename: "{cmd}"; Parameters: "/C taskkill /F /IM EyeGuard.exe /T"; Flags: runhidden

[Code]
// Helper function to terminate running EyeGuard instances during install or uninstall
procedure KillEyeGuardProcess();
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/F /IM EyeGuard.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(250);
end;

function InitializeSetup(): Boolean;
begin
  KillEyeGuardProcess();
  Result := True;
end;

function InitializeUninstall(): Boolean;
begin
  KillEyeGuardProcess();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDataDir: String;
  LocalAppDataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    KillEyeGuardProcess();
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    // Clean up user configuration and webview cache directories completely
    AppDataDir := ExpandConstant('{userappdata}\EyeGuard');
    if DirExists(AppDataDir) then
    begin
      DelTree(AppDataDir, True, True, True);
    end;

    LocalAppDataDir := ExpandConstant('{localappdata}\EyeGuardWebview');
    if DirExists(LocalAppDataDir) then
    begin
      DelTree(LocalAppDataDir, True, True, True);
    end;

    // Also remove the application installation directory if any leftovers remain
    DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;
