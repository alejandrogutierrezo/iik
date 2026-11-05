; Instalador de iik para Windows (Inno Setup 6). Se compila después de PyInstaller:
;
;     pyinstaller instalador/iik.spec --noconfirm
;     iscc /DVersion=0.1.0 instalador\iik.iss
;
; Resultado: dist\iik-<versión>-windows-instalador.exe
;
; Se instala para el usuario actual (sin permisos de administrador), en
; %LOCALAPPDATA%\Programs\iik: sirve en las computadoras de laboratorio donde los alumnos no son
; administradores.

#ifndef Version
  #define Version "0.1.0"
#endif

[Setup]
AppId={{C8146935-B8D5-4D57-AE61-4FDF8A93F86F}
AppName=iik
AppVersion={#Version}
AppVerName=iik {#Version}
AppPublisher=Dr. Alejandro Gutiérrez Ortiz
AppPublisherURL=https://github.com/alejandrogutierrezo/iik
AppComments=Evaluación de recurso eólico (WRA) para parques eólicos en México
DefaultDirName={autopf}\iik
DefaultGroupName=iik
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=iik-{#Version}-windows-instalador
SetupIconFile=iik.ico
UninstallDisplayIcon={app}\iik.exe
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Files]
Source: "..\dist\iik\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Al actualizar, borra la versión anterior para que no queden archivos viejos mezclados.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\iik"; Filename: "{app}\iik.exe"; WorkingDir: "{app}"; Comment: "Evaluación de recurso eólico"
Name: "{group}\Desinstalar iik"; Filename: "{uninstallexe}"
Name: "{autodesktop}\iik"; Filename: "{app}\iik.exe"; WorkingDir: "{app}"; Tasks: escritorio

[Run]
Filename: "{app}\iik.exe"; Description: "Abrir iik ahora"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
