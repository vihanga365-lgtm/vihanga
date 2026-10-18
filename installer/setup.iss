#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef AppExe
  #define AppExe "..\CrowTradingBot.exe"
#endif

[Setup]
AppId={{B1EB6DE5-26B0-47C0-86EC-A23D1B2DD13F}
AppName=Crow Trading Bot
AppVersion={#AppVersion}
AppPublisher=Crow
DefaultDirName={localappdata}\Programs\CrowTradingBot
DefaultGroupName=Crow Trading Bot
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..
OutputBaseFilename=CrowTradingBot-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\CrowTradingBot.exe
CloseApplications=no
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#AppExe}"; DestDir: "{app}"; DestName: "CrowTradingBot.exe"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Crow Trading Bot"; Filename: "{app}\CrowTradingBot.exe"
Name: "{autodesktop}\Crow Trading Bot"; Filename: "{app}\CrowTradingBot.exe"; Tasks: desktopicon

; User settings and trade state under LocalAppData\CrowTradingBot are preserved.
; Installing never starts trading or automatically closes a running bot.
