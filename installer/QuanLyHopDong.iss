; Bộ cài đặt Windows cho Phần mềm Quản lý Hợp đồng (Inno Setup 6).
; Build: iscc /DAppVersion=1.0.1 /DSourceDir=..\dist\QuanLyHopDong /DOutDir=..\out installer\QuanLyHopDong.iss
; Dữ liệu người dùng nằm ở %APPDATA%\QuanLyHopDong, gỡ cài đặt không xóa dữ liệu.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\QuanLyHopDong"
#endif
#ifndef OutDir
  #define OutDir "..\out"
#endif

[Setup]
AppId={{20D7D9DE-09FD-4CFB-A62C-C3ED7599AFD6}
AppName=Quản lý Hợp đồng
AppVersion={#AppVersion}
AppVerName=Quản lý Hợp đồng {#AppVersion}
AppPublisher=Công ty cổ phần Công nghệ ITD Việt Nam
AppPublisherURL=https://itdvietnam.com.vn
AppSupportURL=https://itdvietnam.com.vn
AppContact=vanthu.itdvn@gmail.com · 0988095867
AppCopyright=© 2026 Công ty cổ phần Công nghệ ITD Việt Nam
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\QuanLyHopDong
DefaultGroupName=Quản lý Hợp đồng
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutDir}
OutputBaseFilename=QuanLyHopDong-CaiDat-Windows-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\QuanLyHopDong.exe
UninstallDisplayName=Quản lý Hợp đồng {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng trên màn hình Desktop"; GroupDescription: "Biểu tượng:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Quản lý Hợp đồng"; Filename: "{app}\QuanLyHopDong.exe"
Name: "{group}\Gỡ cài đặt Quản lý Hợp đồng"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Quản lý Hợp đồng"; Filename: "{app}\QuanLyHopDong.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\QuanLyHopDong.exe"; Description: "Mở Quản lý Hợp đồng"; Flags: nowait postinstall skipifsilent
