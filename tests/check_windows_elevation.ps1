#Requires -Version 5.1
# Maintainer probe: an elevated token must be refused without installation.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1')
$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
$admin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Output "ADMINISTRATOR_ROLE: $admin"
if (-not $admin) { throw 'This refusal probe requires an elevated test token.' }
$installer = Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1'
Write-Output ('INSTALLER_SHA256: ' + (Get-FileHash $installer).Hash.ToLowerInvariant())
$prefix = Join-Path $env:TEMP ('abs-elevated-probe-' + [guid]::NewGuid().ToString('N'))
$failure = $null
try { Install-AbsCensus -Prefix $prefix -Version '0.1.0.dev0' }
catch { $failure = $_.Exception.Message }
if ($failure -notmatch 'normal PowerShell' -or (Test-Path -LiteralPath $prefix)) {
    throw 'Elevated refusal failed or created an installation root.'
}
Write-Output ('EXPECTED_REFUSAL: ' + $failure)
Write-Output 'PASS: elevated install refused before root creation or download'
