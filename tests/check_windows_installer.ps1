#Requires -Version 5.1
<# Native online smoke using local real releases; all installations stay under TEMP. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ReleaseDir,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$NextVersion
)
$ErrorActionPreference = 'Stop'
$smokeParameters = @{} + $PSBoundParameters
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1')
# Dot-sourcing parameterized installer declares its own variables; retain inputs separately.
$base = Join-Path $env:TEMP ('abs-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
$root = Join-Path $base "i café '&!%"
$assets = Join-Path $base 'assets'
if ($root.Length -gt 100) {
    throw 'Smoke root exceeds the installer path budget; select a shorter TEMP first.'
}
Write-Host "SMOKE_ROOT_LENGTH: $($root.Length); TEMP_LENGTH: $($env:TEMP.Length)"
$first = $smokeParameters.Version; $next = $smokeParameters.NextVersion
$script:SmokeChecks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "Assertion failed: $Message" }
    $script:SmokeChecks++
}
function Reject([scriptblock]$Action, [string]$Pattern) {
    $errorText = $null
    try { & $Action } catch { $errorText = $_.Exception.Message }
    Check ($null -ne $errorText -and $errorText -match $Pattern) "Expected $Pattern; got $errorText"
}
function Read-Selection { return Read-JsonFile (Join-Path $root 'active.json') }
function Run-App([string[]]$Arguments) {
    $selection = Read-Selection
    $launcher = Join-Path $selection.bin_dir 'abs-census-etl.exe'
    return Invoke-Native $launcher $Arguments $base
}
$realUv = (Get-Command Invoke-Uv).ScriptBlock
$dataHandle = $null
function Get-PythonRegistrySnapshot {
    $path = 'HKCU:\Software\Python'
    if (-not (Test-Path $path)) { return 'absent' }
    $keys = @(Get-Item $path) + @(Get-ChildItem $path -Recurse)
    return (@($keys | Sort-Object Name | ForEach-Object {
        $key = $_
        @($key.Name; @($key.GetValueNames() | Sort-Object | ForEach-Object {
            @($_; $key.GetValueKind($_).ToString(); $key.GetValue($_))
        }))
    }) | ConvertTo-Json -Depth 20 -Compress)
}
$registryBefore = Get-PythonRegistrySnapshot
Assert-StandardUser
Write-Host 'TOKEN: standard (administrator role disabled)'
$installerPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1'
Write-Host ('INSTALLER_SHA256: ' + (Get-FileHash $installerPath).Hash.ToLowerInvariant())
try {
    [void][IO.Directory]::CreateDirectory($base)
    Copy-Item -LiteralPath $smokeParameters.ReleaseDir -Destination $assets -Recurse
    $options = @{ Prefix = $root; ReleaseDir = $assets }
    Install-AbsCensus @options -Version $first
    $original = Read-Selection
    $repeat = Install-AbsCensus @options -Version $first 6>&1 | Out-String
    Check ($repeat -match 'Already installed') 'repeat skips current environment'
    Check ((Read-Selection).slot -ceq $original.slot) 'repeat keeps original slot'
    Install-AbsCensus -Prefix $root.ToUpperInvariant() -ReleaseDir $assets -Version $first
    Check ((Read-Selection).slot -ceq $original.slot) 'case-only prefix repeat'
    $lock = Open-InstallLock (Join-Path $root '.install-lock')
    try { Reject { Install-AbsCensus @options -Version $first } 'lock is busy' }
    finally { $lock.Dispose() }
    $helpText = Run-App @('--help')
    Check ($helpText.ExitCode -eq 0 -and $helpText.Stdout -match 'usage: abs-census-etl') 'help'
    Check ($helpText.Stdout -notmatch 'python abs_census_etl.py') 'managed examples'
    $invalid = Run-App @('--unknown')
    Check ($invalid.ExitCode -eq 2 -and $invalid.Stderr -match 'abs-census-etl: error:') 'CLI error'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $fixture = Join-Path $base 'nt.zip'
    $zip = [IO.Compression.ZipFile]::Open($fixture, 'Create')
    try {
        $entry = $zip.CreateEntry('2021 Census GCP Postal Areas for NT/2021Census_G01_NT_POA.csv')
        $writer = New-Object IO.StreamWriter($entry.Open())
        try { $writer.Write("POA_CODE_2021,Tot_P_P`n0800,100`n0810,400`n") }
        finally { $writer.Dispose() }
    } finally { $zip.Dispose() }
    $csvOut = Join-Path $base 'CSV café'
    $result = Run-App @('--geo', 'POA', '--state', 'NT', '--zip', $fixture,
        '--codes', '0800', '--out', $csvOut)
    Check ($result.ExitCode -eq 0) "CSV export: $($result.Stderr)"
    $csvPath = Join-Path $csvOut '2021_GCP_POA_NT_G01_wide.csv'
    Check (@(Import-Csv -LiteralPath $csvPath)[0].POA_CODE_2021 -ceq '0800') 'CSV leading zero'
    $csvBytes = [Convert]::ToBase64String([IO.File]::ReadAllBytes($csvPath))
    # Keep export open like Excel while changing installed versions.
    $dataHandle = [IO.File]::Open($csvPath, 'Open', 'Read', 'Read')
    $bad = Join-Path $base 'bad-assets'
    Copy-Item -LiteralPath $assets -Destination $bad -Recurse
    [IO.File]::AppendAllText((Join-Path $bad "abs_census_etl-$next.tar.gz"), 'bad')
    Reject { Install-AbsCensus -Prefix $root -ReleaseDir $bad -Version $next } 'checksum mismatch'
    Check ((Read-Selection).slot -ceq $original.slot) 'bad checksum retains active app'
    # Fault injection at an external command boundary; actual environment checks remain real.
    function Invoke-Uv($Context, [string[]]$Arguments, [string]$Directory) {
        if ($Arguments[0] -eq 'sync') { throw 'Injected dependency sync failure' }
        & $realUv $Context $Arguments $Directory
    }
    Reject { Install-AbsCensus @options -Version $next } 'Injected dependency sync failure'
    Set-Item function:Invoke-Uv $realUv
    Check ((Read-Selection).slot -ceq $original.slot) 'failed sync retains pointer'
    Check (@(Get-ChildItem -LiteralPath (Join-Path $root 'versions') -Directory).Count -eq 1) `
        'failed preparation removed its candidate'
    function Invoke-Uv($Context, [string[]]$Arguments, [string]$Directory) {
        if ($Arguments[0] -eq 'pip') {
            $Arguments[-1] = Join-Path $assets ("abs_census_etl-$first-py3-none-any.whl")
        }
        & $realUv $Context $Arguments $Directory
    }
    Reject { Install-AbsCensus @options -Version $next } 'Installed version/import/CLI smoke'
    Set-Item function:Invoke-Uv $realUv
    Check ((Read-Selection).slot -ceq $original.slot) 'wrong installed version refused'
    $lock = [IO.File]::Open((Join-Path $root '.install-lock'), 'CreateNew', 'ReadWrite', 'None')
    try { Reject { Install-AbsCensus @options -Version $next } 'lock is busy|used|process' }
    finally { $lock.Dispose(); Remove-Item -LiteralPath (Join-Path $root '.install-lock') }
    $pointer = Join-Path $root 'active.json'
    $before = [IO.File]::ReadAllText($pointer)
    $held = [IO.File]::Open($pointer, 'Open', 'Read', 'Read')
    try { Reject { Install-AbsCensus @options -Version $next } 'used|process|access' }
    finally { $held.Dispose() }
    Check ([IO.File]::ReadAllText($pointer) -ceq $before) 'failed publication keeps JSON bytes'
    Check ((Run-App @('--help')).ExitCode -eq 0) 'old app works after publication failure'
    $settingsHandle = [IO.File]::Open((Join-Path $root 'settings.json'), 'Open', 'Read', 'Read')
    try {
        $activation = Install-AbsCensus @options -Version $next 3>&1 6>&1 | Out-String
        Check ($activation -match 'App activated; fallback settings') 'settings failure warns'
    } finally { $settingsHandle.Dispose() }
    Check ((Read-Selection).slot -cne $original.slot) 'update changes active slot'
    Check ([IO.Directory]::Exists((Join-Path $root ('versions\' + $original.slot)))) 'old retained'
    Install-AbsCensus @options -Version $next -Parquet
    Check ((Read-Selection).format -ceq 'parquet') 'optional Parquet selected'
    $parquetOut = Join-Path $base 'parquet'
    $result = Run-App @('--geo', 'POA', '--state', 'NT', '--zip', $fixture,
        '--codes', '0800', '--format', 'parquet', '--out', $parquetOut)
    Check ($result.ExitCode -eq 0) "Parquet export: $($result.Stderr)"
    $selection = Read-Selection
    $python = Join-Path $root "versions\$($selection.slot)\source\.venv\Scripts\python.exe"
    $parquetPath = Join-Path $parquetOut '2021_GCP_POA_NT_G01_wide.parquet'
    $probe = "import pandas,sys; " +
        "assert pandas.read_parquet(sys.argv[1]).POA_CODE_2021.tolist()==['0800']"
    [void](Invoke-CheckedNative $python @('-I', '-X', 'utf8', '-c', $probe, $parquetPath) $base)
    Install-AbsCensus @options -Version $next
    Check ((Read-Selection).format -ceq 'parquet') 'plain repeat preserves Parquet'
    $oldLauncher = Join-Path (Read-Selection).bin_dir 'abs-census-etl.exe'
    $held = [IO.File]::Open($oldLauncher, 'Open', 'Read', 'Read')
    $newBin = Join-Path $base 'moved bin'
    try { Install-AbsCensus @options -Version $next -BinDir $newBin }
    finally { $held.Dispose() }
    Check ((Read-Selection).bin_dir -ceq $newBin) 'bin directory move'
    Check ([IO.File]::Exists($oldLauncher)) 'old launcher retained for manual removal'
    Check ((Run-App @('--help')).ExitCode -eq 0) 'new launcher works'
    $fork = Install-AbsCensus @options -Version $next -Repository 'owner/fork' 6>&1 | Out-String
    Check ($fork -match 'Using non-default release repository: owner/fork') 'fork announced'
    Check ((Read-Selection).repository -ceq 'owner/fork') 'fork identity saved'
    $forkRepeat = Install-AbsCensus @options -Version $next 6>&1 | Out-String
    Check ($forkRepeat -match 'owner/fork' -and $forkRepeat -match 'Already installed') `
        'saved fork stays visible on repeat'
    Install-AbsCensus @options -Version $next `
        -Repository 'Agent-Cognitive-Enterprise/abs-census-etl'
    $hidden = Join-Path $base 'assets-hidden'
    Move-Item -LiteralPath $assets -Destination $hidden
    [void][IO.Directory]::CreateDirectory($assets)
    Install-AbsCensus @options -Version $first -Csv
    Check ((Read-Selection).slot -ceq $original.slot) 'asset-free cached rollback'
    Write-AtomicText $pointer ''
    Install-AbsCensus @options -Version $first
    Check ((Read-Selection).slot -ceq $original.slot) 'damaged pointer repair'
    Check ([Convert]::ToBase64String([IO.File]::ReadAllBytes($csvPath)) -ceq $csvBytes) `
        'locked user export preserved byte-for-byte'
    Check (-not (Test-Path -LiteralPath (Join-Path $root '.install-lock'))) 'lock cleanup'
    Check (@(Get-ChildItem -LiteralPath $root -Filter '.install-*').Count -eq 0) 'work cleanup'
    # Test the exact README bootstrap command. Only the installer download is
    # replaced; child PowerShell, argument forwarding and activation remain real.
    $readme = Join-Path (Split-Path $PSScriptRoot -Parent) 'README.md'
    $line = @(Get-Content -LiteralPath $readme | Where-Object {
        $_.StartsWith('& { $installer = ')
    })
    Check ($line.Count -eq 1) 'one Windows bootstrap command in README'
    $bootstrap = [scriptblock]::Create($line[0] + ' @args')
    $script:DownloadCount = 0
    $installerSource = Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1'
    function Invoke-WebRequest {
        param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
        Check ($Uri -like 'https://github.com/*/releases/latest/download/install.ps1') `
            'bootstrap downloads current installer'
        [IO.File]::Copy($installerSource, $OutFile)
        $script:DownloadCount++
    }
    $pathBefore = [Environment]::GetEnvironmentVariable('PATH', 'User')
    $policyBefore = Get-ExecutionPolicy -List | ConvertTo-Json -Compress
    try {
        & $bootstrap -Prefix $root -ReleaseDir $assets -Version $next -Parquet
        Check ((Read-Selection).format -ceq 'parquet') 'README options forwarded'
        & $bootstrap -Prefix $root -ReleaseDir $assets -Version $next
        Check ((Read-Selection).format -ceq 'parquet') 'README update preserves options'
        Check ($script:DownloadCount -eq 2) 'README downloads afresh every run'
    } finally { Remove-Item function:Invoke-WebRequest }
    Check ([Environment]::GetEnvironmentVariable('PATH', 'User') -ceq $pathBefore) `
        'persistent user PATH unchanged'
    Check ((Get-ExecutionPolicy -List | ConvertTo-Json -Compress) -ceq $policyBefore) `
        'execution policy unchanged'
    Check ((Get-PythonRegistrySnapshot) -ceq $registryBefore) 'HKCU Python unchanged'
    Write-Host "PASS: $script:SmokeChecks real Windows installation/update/failure assertions"
} finally {
    Set-Item function:Invoke-Uv $realUv
    if ($null -ne $dataHandle) { $dataHandle.Dispose() }
    if ([IO.Directory]::Exists($base)) { Remove-Item -LiteralPath $base -Recurse -Force }
}
