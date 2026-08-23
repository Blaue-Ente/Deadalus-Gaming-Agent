; Inno Setup 6 — compile on Windows to get Дедал-Setup.exe
;   iscc packaging\windows\Daedalus.iss
; The installer is per-user (no Administrator). After files are extracted
; it runs install.ps1 -InPlace to create the venv and Start Menu shortcut.

#define MyAppName "Дедал"
#define MyAppNameEn "Daedalus"
#define MyAppVersion "0.3.2"
#define MyAppPublisher "Blaue-Ente"
#define MyAppURL "https://gitlab.com/Blaue-Ente/deadalus-agent-0"

[Setup]
AppId={{8F3C2A91-6D44-4B1E-9E77-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist
OutputBaseFilename=Daedalus-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; \
  Excludes: ".git\*,.venv\*,data\*,playground\*,__pycache__\*,.pytest_cache\*,daedalus.egg-info\*,dist\*,.agents\*,*.pyc,.env"

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\windows\install.ps1"" -InPlace"; \
  StatusMsg: "Creating Python venv and Start Menu shortcut…"; Flags: waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\uninstall.ps1"""; \
  RunOnceId: "DaedalusUninstall"; Flags: waituntilterminated

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\Daedalus.vbs"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
