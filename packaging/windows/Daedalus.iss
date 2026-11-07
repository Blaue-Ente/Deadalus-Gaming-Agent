; Inno Setup 6 — compile on Windows:
;   powershell -File packaging\windows\build-setup.ps1
;   (or: iscc packaging\windows\Daedalus.iss)
;
; Per-user, no Administrator. After files are extracted, install.ps1
; -InPlace -BundlePython installs a private CPython + venv; shortcuts use Daedalus.vbs.

#define MyAppName "Daedalus"
#define MyAppNameEn "Daedalus"
#define MyAppVersion "0.3.4"
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
MinVersion=10.0
LicenseFile=..\..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; \
  Excludes: ".git\*,.venv\*,data\*,playground\*,__pycache__\*,.pytest_cache\*,daedalus.egg-info\*,dist\*,.agents\*,*.pyc,.env,runtime\*"
Source: "redist\*"; DestDir: "{app}\packaging\windows\redist"; Flags: ignoreversion skipifsourcedoesntexist

[Run]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\packaging\windows\install.ps1"" -InPlace -BundlePython"; \
  StatusMsg: "Installing private Python, venv, and Start Menu shortcuts..."; Flags: waituntilterminated

[UninstallRun]
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\uninstall.ps1"""; \
  RunOnceId: "DaedalusUninstall"; Flags: waituntilterminated

[Icons]
; App shortcut is created by install.ps1 (Daedalus.vbs primary launch path).
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
