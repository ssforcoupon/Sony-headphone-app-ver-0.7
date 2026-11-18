; Inno Setup script (https://jrsoftware.org/isinfo.php). Build:  iscc /DAppVersion=0.2.1 installer\SonyBridgeWindows.iss
; Expects dist\SonyBridgeWindows.exe (PyInstaller output). Produces dist\SonyHeadphonesWidget-Setup.exe
#define AppName "Sony Headphones Widget"
#define AppExe "SonyBridgeWindows.exe"
#ifndef AppVersion
  #define AppVersion "0.2.1"
#endif

[Setup]
AppId={{B7C0A3E2-5C1D-4E0B-9B7E-5A3D2F6C1A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=SonyBridgeWindows
; Per-user install by default: no administrator rights needed. The wizard offers "all users" if wanted.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\SonyHeadphonesWidget
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\dist
OutputBaseFilename=SonyHeadphonesWidget-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; The app holds this mutex while running: setup asks the user to close it instead of failing on a locked file.
AppMutex=SonyBridgeWindows_Single
CloseApplications=yes
RestartApplications=no

[Tasks]
; Ticked by default. The same Run-key value the app's own "Start with Windows" menu item uses, so both stay in sync.
Name: "startup"; Description: "Start the widget automatically when I sign in to Windows"
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "SonyBridgeWindows"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: startup
; Re-running setup with the box unticked turns startup off again.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueName: "SonyBridgeWindows"; Flags: deletevalue; Tasks: not startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start the widget now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Settings (%APPDATA%\SonyBridgeWindows) are kept on purpose; delete that folder by hand for a clean slate.
Type: filesandordirs; Name: "{app}"
