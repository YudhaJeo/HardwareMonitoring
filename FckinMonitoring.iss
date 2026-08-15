; =====================================================================
; FckinMonitoring - Inno Setup Script
; Build exe dulu (lihat hardware_monitor.spec) sebelum compile ini.
; =====================================================================
#define MyAppName "FckinMonitoring"
#define MyAppVersion "3.0.0"
#define MyAppPublisher "YourName"
#define MyAppExeName "FckinMonitoring.exe"
#define MyAppIcon "compile\icon.ico"

[Setup]
AppId={{B3C1F1A0-9A4E-4E56-9ABC-FC0000000001}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=versions
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
SetupIconFile={#MyAppIcon}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Buat shortcut di Desktop"; GroupDescription: "Shortcut tambahan:"
Name: "startupicon"; Description: "Jalankan otomatis saat Windows startup"; GroupDescription: "Opsi startup:"

[Files]
; Seluruh isi folder On-Directory PyInstaller (Executable + _internal)
Source: "compile\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Asset tambahan (seperti icon.png)
Source: "assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Jalankan {#MyAppName} sekarang"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"