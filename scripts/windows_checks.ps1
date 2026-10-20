#Requires -Version 5.1
# Invoked by with_standard_user.ps1. Keep TEMP under the caller's trusted profile.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$env:PYTHONUTF8='1'
$root=Split-Path $PSScriptRoot -Parent
Set-Location $root
. (Join-Path $root 'install.ps1')
Assert-StandardUser
Write-Output ('ADMINISTRATOR_ROLE: False; PS_VERSION: '+$PSVersionTable.PSVersion)
# Do not rewrite an existing TEMP DACL: it may intentionally be shared.
$ciTemp=Join-Path $env:LOCALAPPDATA ('abs-ci-'+[guid]::NewGuid().ToString('N').Substring(0,8))
New-PrivateDirectory $ciTemp
$env:TEMP=$ciTemp;$env:TMP=$ciTemp
try {
$python=Join-Path $root '.venv\Scripts\python.exe'
$env:ABS_INSTALLER_TEST_PYTHON=$python
& $python -m unittest discover -s tests -v
if($LASTEXITCODE -ne 0){throw 'Windows unit suite failed'}
& $python tests/check_windows_installer.py --release-dir dist/release
if($LASTEXITCODE -ne 0){throw 'PowerShell 5.1 full smoke failed'}
$pwsh=(Get-Command pwsh.exe -CommandType Application -ErrorAction Stop).Source
& $pwsh -NoProfile -ExecutionPolicy Bypass -File tests/test_windows_installer.ps1 -Python $python
if($LASTEXITCODE -ne 0){throw 'PowerShell 7 offline suite failed'}
& $python tests/check_windows_installer.py --release-dir dist/release --powershell $pwsh
if($LASTEXITCODE -ne 0){throw 'PowerShell 7 full smoke failed'}
} finally {
    try { Remove-Item -LiteralPath $ciTemp -Recurse -Force }
    catch { Write-Warning "Disposable CI temp needs cleanup: $ciTemp" }
}
