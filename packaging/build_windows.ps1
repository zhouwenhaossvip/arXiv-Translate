param(
    [string]$IsccPath = ""
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repo

$pythonExe = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python 3.11+ is required on the build machine."
    }
    $pythonExe = $pythonCommand.Source
}
& $pythonExe -m PyInstaller --version
if ($LASTEXITCODE -ne 0) {
    throw "Install PyInstaller first: $pythonExe -m pip install pyinstaller"
}

& (Join-Path $PSScriptRoot "generate_installer_assets.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Installer artwork generation failed."
}
& $pythonExe -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name arXiv-Translate `
    --icon "packaging/generated/app.ico" `
    --add-data "packaging/generated/app.ico;packaging/generated" `
    --add-data "web;web" `
    --add-data "arxiv_translate/assets;arxiv_translate/assets" `
    arxiv_translate/desktop.py
if ($LASTEXITCODE -ne 0) {
    throw "Desktop app packaging failed."
}
$appExe = Join-Path $repo "dist\arXiv-Translate\arXiv-Translate.exe"
$selfTest = Start-Process -FilePath $appExe -ArgumentList "--self-test" -WindowStyle Hidden -Wait -PassThru
if ($selfTest.ExitCode -ne 0) {
    throw "Frozen desktop app resource self-test failed."
}

if (-not $IsccPath) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        $IsccPath = $command.Source
    } else {
        $candidates = @(
            "$env:USERPROFILE\AppData\Local\Programs\Inno Setup 7\ISCC.exe",
            "${env:ProgramFiles}\Inno Setup 7\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe"
        )
        $IsccPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    }
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath)) {
    throw "App built in dist/arXiv-Translate, but ISCC.exe was not found. Install Inno Setup 7 or pass -IsccPath."
}

& $IsccPath (Join-Path $repo "packaging\windows.iss")
if ($LASTEXITCODE -ne 0) {
    throw "Installer build failed."
}
Write-Host "Installer built in dist/installer."
