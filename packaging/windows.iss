#define AppName "arXiv Translate"
#define AppVersion "0.1.0"

[Setup]
AppId={{7B106D7F-EE0A-4F5B-84E5-0F94C6276AF4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=arXiv Translate contributors
DefaultDirName={localappdata}\Programs\arXiv Translate
DisableDirPage=no
DefaultGroupName={#AppName}
OutputDir=..\dist\installer
OutputBaseFilename=arXiv-Translate-Setup-{#AppVersion}-Windows
Compression=lzma2
SolidCompression=yes
ArchiveExtraction=full
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
UninstallDisplayIcon={app}\arXiv-Translate.exe
WizardStyle=modern windows11
WizardSizePercent=125,125
DisableWelcomePage=no
ShowLanguageDialog=auto
WizardImageFile=generated\wizard-large.png
WizardSmallImageFile=generated\wizard-small.png
SetupIconFile=generated\app.ico

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
chinesesimplified.WelcomeLabel2=安装 arXiv Translate。%n%n默认会联网下载并配置缺少的 LaTeX、Perl 和中文字体；已有组件会跳过。下载可能需要一段时间。
chinesesimplified.FinishedLabel=arXiv Translate 已安装完成。现在可以启动程序检查编译环境并开始翻译。
chinesesimplified.FinishedLabelNoIcons=arXiv Translate 已安装完成。现在可以启动程序检查编译环境。
english.WelcomeLabel2=Install arXiv Translate. By default, Setup downloads and configures missing LaTeX, Perl, and Chinese fonts. Existing components are skipped.
english.FinishedLabel=arXiv Translate is installed. Launch it to verify the LaTeX environment and start translating.
english.FinishedLabelNoIcons=arXiv Translate is installed. Launch it to verify the LaTeX environment.

[CustomMessages]
chinesesimplified.AutoDependencies=联网自动安装缺少的编译环境（最大约 470 MB）
english.AutoDependencies=Download and install missing compiler components (up to 470 MB)
chinesesimplified.DependencyGroup=编译环境：
english.DependencyGroup=Compiler environment:
chinesesimplified.DesktopIcon=创建桌面快捷方式
english.DesktopIcon=Create a desktop shortcut
chinesesimplified.OtherOptions=附加选项：
english.OtherOptions=Additional options:
chinesesimplified.InstallingMiKTeX=正在为当前用户安装 MiKTeX。这可能需要几分钟…
english.InstallingMiKTeX=Installing MiKTeX for the current user. This may take several minutes...
chinesesimplified.DependencyDownloadFailed=依赖下载或校验失败。请检查网络后重试，也可取消自动安装，稍后在程序中手动配置。
english.DependencyDownloadFailed=The dependency download or verification failed. Check your connection and retry, or disable automatic setup and configure it later.
chinesesimplified.MiKTeXInstallFailed=MiKTeX 自动安装失败（退出码 %1）。可返回取消自动安装，之后按程序提示手动安装。
english.MiKTeXInstallFailed=MiKTeX setup failed (exit code %1). You can go back, disable automatic setup, and install it manually later.

[Files]
Source: "..\dist\arXiv-Translate\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{tmp}\strawberry-perl-portable.zip"; DestDir: "{app}\tools\strawberry"; Flags: external extractarchive recursesubdirs createallsubdirs ignoreversion; Check: InstallPerl
Source: "{tmp}\NotoSerifSC-VF.ttf"; DestDir: "{autofonts}"; FontInstall: "Noto Serif SC"; Flags: external onlyifdoesntexist uninsneveruninstall; Check: InstallFont

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\arXiv-Translate.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\arXiv-Translate.exe"; Tasks: desktopicon

[Tasks]
Name: "autodeps"; Description: "{cm:AutoDependencies}"; GroupDescription: "{cm:DependencyGroup}"
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:OtherOptions}"; Flags: unchecked

[Run]
Filename: "{app}\arXiv-Translate.exe"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  MiKTeXUrl = 'https://miktex.org/download/ctan/systems/win32/miktex/setup/windows-x64/basic-miktex-25.12-x64.exe';
  MiKTeXSha256 = '14b42dd9f4b4a7813a8bfd69c8f99316c2888cc4ee26f631f397e163d85d6c62';
  PerlUrl = 'https://github.com/StrawberryPerl/Perl-Dist-Strawberry/releases/download/SP_54231_64bit/strawberry-perl-5.42.3.1-64bit-portable.zip';
  PerlSha256 = '6a081a811781c30aca51dbc036afd93092af91e3297901f02c17043795a10690';
  FontUrl = 'https://raw.githubusercontent.com/notofonts/noto-cjk/Serif2.003/Serif/Variable/TTF/Subset/NotoSerifSC-VF.ttf';
  FontSha256 = '5326cfb097e3ab26fcb39329752b5c0a439bf8d5c4649520e4b492939c352a09';

var
  DownloadPage: TDownloadWizardPage;
  InstallMiKTeX: Boolean;
  InstallPerlNow: Boolean;
  InstallFontNow: Boolean;

function OnPath(const FileName: String): Boolean;
var
  SearchPath: String;
  Part: String;
  Separator: Integer;
begin
  SearchPath := GetEnv('PATH') + ';' + ExpandConstant('{localappdata}\Programs\MiKTeX\miktex\bin\x64') +
    ';' + ExpandConstant('{app}\tools\strawberry\perl\bin') + ';';
  Result := False;
  while SearchPath <> '' do begin
    Separator := Pos(';', SearchPath);
    if Separator = 0 then Break;
    Part := Trim(Copy(SearchPath, 1, Separator - 1));
    Delete(SearchPath, 1, Separator);
    if (Part <> '') and FileExists(AddBackslash(Part) + FileName) then begin
      Result := True;
      Exit;
    end;
  end;
end;

function InstallPerl: Boolean;
begin
  Result := InstallPerlNow;
end;

function InstallFont: Boolean;
begin
  Result := InstallFontNow;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc), nil);
  DownloadPage.ShowBaseNameInsteadOfUrl := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID <> wpReady then Exit;
  InstallMiKTeX := False;
  InstallPerlNow := False;
  InstallFontNow := False;
  if not WizardIsTaskSelected('autodeps') then Exit;

  InstallMiKTeX := not (OnPath('latexmk.exe') and OnPath('pdflatex.exe') and
    OnPath('xelatex.exe') and OnPath('lualatex.exe'));
  InstallPerlNow := not OnPath('perl.exe');
  InstallFontNow := not FontExists('Noto Serif SC') and
    not FileExists(ExpandConstant('{autofonts}\NotoSerifSC-VF.ttf'));
  DownloadPage.Clear;
  if InstallMiKTeX then
    DownloadPage.Add(MiKTeXUrl, 'basic-miktex.exe', MiKTeXSha256);
  if InstallPerlNow then
    DownloadPage.Add(PerlUrl, 'strawberry-perl-portable.zip', PerlSha256);
  if InstallFontNow then
    DownloadPage.Add(FontUrl, 'NotoSerifSC-VF.ttf', FontSha256);
  if not (InstallMiKTeX or InstallPerlNow or InstallFontNow) then Exit;

  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
    except
      if not DownloadPage.AbortedByUser then
        SuppressibleMsgBox(CustomMessage('DependencyDownloadFailed') + #13#10 +
          DownloadPage.LastBaseNameOrUrl + ': ' + GetExceptionMessage,
          mbCriticalError, MB_OK, IDOK);
      Result := False;
    end;
  finally
    DownloadPage.Hide;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  ConfigureExitCode: Integer;
  MiKTeXPath: String;
  InstallDir: String;
  InitexmfPath: String;
begin
  Result := '';
  if not InstallMiKTeX then Exit;
  MiKTeXPath := ExpandConstant('{tmp}\basic-miktex.exe');
  InstallDir := ExpandConstant('{localappdata}\Programs\MiKTeX');
  WizardForm.StatusLabel.Caption := CustomMessage('InstallingMiKTeX');
  WizardForm.Update;
  if not Exec(MiKTeXPath, '--unattended --private --user-install="' + InstallDir + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    Result := FmtMessage(CustomMessage('MiKTeXInstallFailed'), ['could not start'])
  else if ExitCode <> 0 then
    Result := FmtMessage(CustomMessage('MiKTeXInstallFailed'), [IntToStr(ExitCode)]);
  if Result <> '' then Exit;
  InitexmfPath := AddBackslash(InstallDir) + 'miktex\bin\x64\initexmf.exe';
  if FileExists(InitexmfPath) then begin
    if not Exec(InitexmfPath, '--set-config-value [MPM]AutoInstall=1', '',
      SW_HIDE, ewWaitUntilTerminated, ConfigureExitCode) then
      Log('Could not start initexmf to enable on-demand packages.')
    else if ConfigureExitCode <> 0 then
      Log('initexmf on-demand package configuration exited with ' + IntToStr(ConfigureExitCode));
  end else
    Log('MiKTeX installed, but initexmf.exe was not found at the chosen user installation path.');
end;
