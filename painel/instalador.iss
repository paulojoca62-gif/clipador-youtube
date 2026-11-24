; Instalador do CortesEX-PRESS (Inno Setup 6).
; Monta a partir da pasta dist\CortesEX-PRESS gerada pelo PyInstaller.
; Instala só para o usuário atual (não pede administrador) e cria o atalho na área de trabalho.

#define Nome "CortesEX-PRESS"
#ifndef Versao
  #define Versao "1.0.0"
#endif

[Setup]
AppId={{6E2B3C55-2F0A-4C1B-9E55-C0A7E5C0EC51}
AppName={#Nome}
AppVersion={#Versao}
AppPublisher={#Nome}
DefaultDirName={localappdata}\Programs\{#Nome}
DefaultGroupName={#Nome}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=saida
OutputBaseFilename=CortesEX-PRESS-Instalador-{#Versao}
SetupIconFile=assets\cortesex.ico
UninstallDisplayIcon={app}\CortesEX-PRESS.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"
Name: "iniciar"; Description: "Abrir o {#Nome} quando o Windows iniciar"; GroupDescription: "Atalhos:"; Flags: unchecked

[Files]
Source: "dist\CortesEX-PRESS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#Nome}"; Filename: "{app}\CortesEX-PRESS.exe"
Name: "{group}\Desinstalar {#Nome}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#Nome}"; Filename: "{app}\CortesEX-PRESS.exe"; Tasks: desktopicon
Name: "{userstartup}\{#Nome}"; Filename: "{app}\CortesEX-PRESS.exe"; Tasks: iniciar

[Run]
Filename: "{app}\CortesEX-PRESS.exe"; Description: "Abrir o {#Nome} agora"; Flags: nowait postinstall skipifsilent
; Atualização automática (/VERYSILENT /REABRIR): reabre o programa sem abrir o navegador
Filename: "{app}\CortesEX-PRESS.exe"; Parameters: "--sem-navegador"; Flags: nowait runasoriginaluser; Check: Reabrir

[Code]
// Antes de copiar: encerra TODOS os CortesEX-PRESS.exe (o painel e as gravações, que usam o mesmo .exe
// com --streamlink). Sem isso dá "DeleteFile falhou; código 5 / Acesso negado" e, em modo silencioso,
// a atualização desiste sem avisar.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Codigo: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM CortesEX-PRESS.exe', '', SW_HIDE,
       ewWaitUntilTerminated, Codigo);
  Sleep(1500);
  Result := '';
end;

function Reabrir: Boolean;
begin
  Result := WizardSilent and (Pos('/REABRIR', Uppercase(GetCmdTail)) > 0);
end;

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /F /T /IM CortesEX-PRESS.exe"; Flags: runhidden; RunOnceId: "Encerrar"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""CortesEX-PRESS reabrir"" /F"; Flags: runhidden; RunOnceId: "Tarefa"
