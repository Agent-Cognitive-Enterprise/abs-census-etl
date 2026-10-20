#Requires -Version 5.1
# Offline native tests. Development Python is required; no Pester or network.
param([string]$Python = $env:ABS_INSTALLER_TEST_PYTHON)
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1')
$developmentPython = $Python
if (-not $developmentPython) {
    throw 'Pass -Python ABSOLUTE_PATH or set ABS_INSTALLER_TEST_PYTHON to development Python.'
}
$developmentPython = [IO.Path]::GetFullPath($developmentPython)
$base = Join-Path $env:TEMP ('u-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
[void][IO.Directory]::CreateDirectory($base)
$script:Checks = 0
function Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "Assertion failed: $Message" }
    $script:Checks++
    Write-Host "PASS: $Message"
}
function Test-ProcessStopped([int]$ProcessId) {
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $true }
    try { return $process.WaitForExit(5000) } finally { $process.Dispose() }
}
function Reject([scriptblock]$Action, [string]$Pattern) {
    $failure = $null
    try { & $Action } catch { $failure = $_.Exception.Message }
    Check ($null -ne $failure -and $failure -match $Pattern) "Expected $Pattern; got $failure"
}
try {
    foreach ($version in @('0.1.0.dev0', '1.0', '1.2.3rc1.post2.dev3', '2.0.post0')) {
        Check (Test-ReleaseVersion $version) "valid version: $version"
    }
    foreach ($version in @('', '1.0-rc1', '1.0RC1', '01.0', '1.0+local', '1!1.0',
                          "1.0`n", '1.0%2Blocal', '1.0_dev1')) {
        Check (-not (Test-ReleaseVersion $version)) "invalid version: $version"
    }
    foreach ($path in @('C:', 'C:relative', 'C:\', '\\server\share\app', 'C:\bad\..\app',
                       'C:\app.', 'C:\CON', 'C:\app:stream', "C:\app`n", "C:\app`r")) {
        Reject { Get-InstallPath $path } 'path|root|component|control'
    }
    Check ((Get-InstallPath 'C:\space and apostrophe ''\Unicode café\') -ceq
        'C:\space and apostrophe ''\Unicode café') 'literal Unicode paths'
    $private = Join-Path $base 'private'
    $parentAcl = (Get-Acl -LiteralPath $base).Sddl
    [void][IO.Directory]::CreateDirectory($private)
    Set-PrivateDirectory $private
    function Set-Acl { throw 'Identical private ACL should not be rewritten' }
    try { Set-PrivateDirectory $private } finally { Remove-Item function:Set-Acl }
    Check $true 'identical private DACL not rewritten'
    $acl = Get-Acl -LiteralPath $private
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    Check ($acl.GetOwner([Security.Principal.SecurityIdentifier]) -eq $sid) 'private owner'
    foreach ($rule in $acl.GetAccessRules($true, $true,
        [Security.Principal.SecurityIdentifier])) {
        Check ($rule.IdentityReference -in @($sid,
            [Security.Principal.SecurityIdentifier]'S-1-5-18')) 'private DACL identities'
    }
    Check ((Get-Acl -LiteralPath $base).Sddl -ceq $parentAcl) 'ancestor ACL unchanged'
    # Every missing component is born private, without a shared-DACL window.
    $nested = Join-Path $base 'new parent\new child'
    New-PrivateDirectory $nested
    foreach ($created in @((Split-Path $nested -Parent), $nested)) {
        $createdAcl = Get-Acl -LiteralPath $created
        $createdRules = @($createdAcl.GetAccessRules($true, $true,
            [Security.Principal.SecurityIdentifier]))
        Check ($createdAcl.AreAccessRulesProtected -and $createdRules.Count -eq 2 -and
            $createdAcl.GetOwner([Security.Principal.SecurityIdentifier]) -eq $sid) `
            'new directory created private'
    }
    function Set-Acl { throw 'A directory created private should not be rewritten' }
    try { Set-PrivateDirectory $nested; New-PrivateDirectory $nested }
    finally { Remove-Item function:Set-Acl }
    Check $true 'created DACL matches private comparison; existing directory kept'
    # Resolve the real service SID rather than copying the installer's constant.
    $serviceSid = (New-Object Security.Principal.NTAccount('NT SERVICE','TrustedInstaller')).
        Translate([Security.Principal.SecurityIdentifier])
    $ownerFixture = Join-Path $base 'trusted-service-owner'
    New-PrivateDirectory $ownerFixture
    $ownerAcl = New-Object Security.AccessControl.DirectorySecurity
    $ownerAcl.SetOwner($serviceSid)
    $realGetAcl = Get-Command Get-Acl
    function Get-Acl([string]$LiteralPath) {
        if ($LiteralPath -eq $ownerFixture) { return $ownerAcl }
        & $realGetAcl -LiteralPath $LiteralPath
    }
    try {
        Assert-ProtectedLocation $ownerFixture -Stop $base
        Check $true 'Windows-resolved TrustedInstaller owner accepted'
        $ownerAcl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-21-1-2-3-4567')))
        Reject { Assert-ProtectedLocation $ownerFixture -Stop $base } 'Other accounts'
        Check $true 'foreign owner refused through the same Get-Acl mock'
    } finally { Remove-Item function:Get-Acl }

    # One Set-Acl per directory: rewriting a protected DACL can need extra privilege.
    function New-SharedTestDirectory([string]$Name, [string]$Rights, [switch]$InheritOnly) {
        $path = Join-Path $base $Name
        [void][IO.Directory]::CreateDirectory($path)
        $shareAcl = New-Object Security.AccessControl.DirectorySecurity
        $shareAcl.SetOwner($sid); $shareAcl.SetAccessRuleProtection($true, $false)
        $shareAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')))
        $flags = if ($InheritOnly) { 'InheritOnly' } else { 'None' }
        # S-1-5-11 is Authenticated Users, as on a default C:\ or data-drive root.
        $shareAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            [Security.Principal.SecurityIdentifier]'S-1-5-11', $Rights,
            'ContainerInherit,ObjectInherit', $flags, 'Allow')))
        Set-Acl -LiteralPath $path -AclObject $shareAcl
        return $path
    }
    Assert-ProtectedLocation (Join-Path $base 'plain\bin') -Contents
    Check $true 'TEMP-derived location accepted'
    $files = New-SharedTestDirectory 'shared-files' 'CreateFiles'
    Reject { Assert-ProtectedLocation $files -Contents } 'Other accounts'
    Assert-ProtectedLocation (Join-Path $files 'bin') -Contents
    Check $true 'others may add entries above a new bin folder'
    $inherited = New-SharedTestDirectory 'shared-inherit' 'Modify' -InheritOnly
    Reject { Assert-ProtectedLocation $inherited -Contents } 'Other accounts'
    Assert-ProtectedLocation (Join-Path $inherited 'bin') -Contents
    Check $true 'inherit-only parent grants do not refuse a private child'
    $modify = New-SharedTestDirectory 'shared-modify' 'Modify'
    Reject { Assert-ProtectedLocation (Join-Path $modify 'bin\deeper') } 'Other accounts'
    $insideRoot = Join-Path $modify 'root'
    Assert-ProtectedLocation (Join-Path $insideRoot 'bin') -Contents -Stop $insideRoot
    Check $true 'bin inside the root defers to root checks'
    $realArchitecture = (Get-Command Get-NativeArchitecture).ScriptBlock
    try {
        function Get-NativeArchitecture { return 0xaa64 }
        Reject { Get-ManagedTarget } 'native x64'
        function Get-NativeArchitecture { return 0x14c }
        Reject { Get-ManagedTarget } 'native x64'
    } finally { Set-Item function:Get-NativeArchitecture $realArchitecture }
    $atomic = Join-Path $base 'atomic.json'
    Write-AtomicText $atomic 'old'
    Write-AtomicText $atomic 'new'
    Check ([IO.File]::ReadAllText($atomic) -ceq 'new') 'atomic replace'
    $handle = [IO.File]::Open($atomic, 'Open', 'Read', 'Read')
    try { Reject { Write-AtomicText $atomic 'blocked' } 'used|process|access|replace' }
    finally { $handle.Dispose() }
    Check ([IO.File]::ReadAllText($atomic) -ceq 'new') 'locked file retained'
    Check (@(Get-ChildItem -LiteralPath $base -Filter '.part-*').Count -eq 0) 'temp cleanup'
    $lockPath = Join-Path $base 'lock'
    [IO.File]::WriteAllText($lockPath, 'left by older installer')
    $heldLock = Open-InstallLock $lockPath
    try { Reject { Open-InstallLock $lockPath } 'lock is busy' }
    finally { $heldLock.Dispose() }
    Check (-not [IO.File]::Exists($lockPath)) 'OS removes lock on close'
    $again = Open-InstallLock $lockPath; $again.Dispose()
    Check (-not [IO.File]::Exists($lockPath)) 'lock reacquired'
    $context = [pscustomobject]@{ Work = $base; ReleaseDir = $base; AssetUrl = '' }
    # Separate input and verified-output directories: no network for these tests.
    $inputDir = Join-Path $base 'assets'; [void][IO.Directory]::CreateDirectory($inputDir)
    $context.ReleaseDir = $inputDir
    [IO.File]::WriteAllText((Join-Path $inputDir 'asset.whl'), 'wheel bytes')
    $hash = (Get-FileHash -LiteralPath (Join-Path $inputDir 'asset.whl')).Hash.ToLowerInvariant()
    foreach ($sums in @('', "$hash  asset.whl`n$hash  asset.whl", "bad  asset.whl")) {
        [IO.File]::WriteAllText((Join-Path $base 'SHA256SUMS'), $sums)
        Reject { Get-VerifiedAsset $context 'asset.whl' } 'entry'
    }
    [IO.File]::WriteAllText((Join-Path $base 'SHA256SUMS'), ('0' * 64 + '  asset.whl'))
    Reject { Get-VerifiedAsset $context 'asset.whl' } 'checksum mismatch'
    Remove-Item -LiteralPath (Join-Path $base 'asset.whl')
    [IO.File]::WriteAllText((Join-Path $base 'SHA256SUMS'), "$hash  asset.whl")
    Check ([IO.File]::Exists((Get-VerifiedAsset $context 'asset.whl'))) 'verified asset'
    $target = Join-Path $base 'junction-target'
    [void][IO.Directory]::CreateDirectory($target)
    $junction = Join-Path $base 'junction'
    [void](New-Item -ItemType Junction -Path $junction -Target $target)
    Reject { Assert-PlainPath (Join-Path $junction 'child') } 'Reparse'
    # Simulate adversarial tar listings at the command boundary; no extraction occurs.
    $native = (Get-Command Invoke-CheckedNative).ScriptBlock
    try {
        function Invoke-CheckedNative($File, $Arguments, $Directory) {
            if ($Arguments[0] -eq '-tzf') { return $script:Members }
            return $script:Types
        }
        foreach ($member in @('abs_census_etl-1.0/../escape',
                              'abs_census_etl-1.0/foreign+local.py',
                              'abs_census_etl-1.0/epoch!file.py', '/absolute',
                              'other-root/file.py')) {
            $script:Members = $member; $script:Types = '-rw-r--r-- file.py'
            Reject { Assert-SourceArchive 'unused.tar.gz' '1.0' $base } 'Unsafe'
        }
        $script:Members = 'abs_census_etl-1.0/abs_census_etl.py'
        foreach ($type in @('lrwxrwxrwx linked', 'hrw-r--r-- hardlink', 'crw-r--r-- device')) {
            $script:Types = $type
            Reject { Assert-SourceArchive 'unused.tar.gz' '1.0' $base } 'links/special'
        }
    } finally { Set-Item function:Invoke-CheckedNative $native }
    $echoProbe = @'
import json, os, sys
if sys.argv[1:] == ['env']:
    names = ('UV_INDEX_URL', 'PIP_INDEX_URL', 'UV_PROJECT_ENVIRONMENT', 'UV_OVERRIDE',
             'PYTHONPATH', 'APPDATA', 'PROGRAMDATA', 'SSL_CERT_FILE', 'UV_SYSTEM_CERTS',
             'UV_PYTHON_INSTALL_REGISTRY')
    print(json.dumps({name: os.environ.get(name) for name in names}))
else:
    print(json.dumps(sys.argv[1:]))
'@
    $echo = $developmentPython
    $arguments = @('', 'space value', 'café', "apostrophe ' literal", '"quoted"',
                   'C:\space path\', 'C:\slash\\"quote', '&|<>^%!')
    $result = Invoke-CheckedNative $echo (@('-I', '-c', $echoProbe) + $arguments) $base
    $actual = ConvertFrom-Json -InputObject $result
    Check ($actual.Count -eq $arguments.Count) 'native argv count'
    for ($index = 0; $index -lt $arguments.Count; $index++) {
        Check ($actual[$index] -ceq $arguments[$index]) "native argv[$index]"
    }
    $root = Join-Path $base "root space café µ ſ '"
    [void][IO.Directory]::CreateDirectory($root); Set-PrivateDirectory $root
    $bin = Join-Path $base 'external bin'; [void][IO.Directory]::CreateDirectory($bin)
    $slot = 'release-' + [guid]::NewGuid().ToString('N')
    $python = Join-Path $root "versions\$slot\source\.venv\Scripts\python.exe"
    $testVenv = Split-Path (Split-Path $python -Parent) -Parent
    [void](Invoke-CheckedNative $developmentPython @('-I', '-m', 'venv', '--without-pip',
        $testVenv) $base)
    $module = Join-Path $testVenv 'Lib\site-packages\abs_census_etl.py'
    $moduleText = @'
import json, sys, pathlib, time
if sys.argv[-1:] == ['sleep']:
    pathlib.Path(sys.argv[-2]).write_text('ready')
    deadline = time.monotonic() + 120
    while not pathlib.Path(sys.argv[-2] + '.release').exists():
        if time.monotonic() > deadline:
            raise SystemExit('Upgrade test barrier timed out')
        time.sleep(0.05)
if not sys.flags.isolated or not sys.flags.utf8_mode:
    raise SystemExit('Launcher must enable isolation and UTF-8')
if sys.argv[-1:] == ['fail7']:
    raise SystemExit(7)
print(json.dumps(sys.argv[1:]))
'@
    [IO.File]::WriteAllText($module, $moduleText)
    Write-AtomicText (Join-Path $root 'active.json') (@{ slot = $slot } | ConvertTo-Json)
    $realSource = (Get-Command Get-LauncherSource).ScriptBlock
    function Get-LauncherSource($Root) { return ((& $realSource $Root) + "`n// older launcher") }
    $launcher = Ensure-Launcher $root $bin $base
    $output = Invoke-CheckedNative $launcher $arguments $base
    $actual = ConvertFrom-Json -InputObject $output
    $expected = $arguments
    Check ($actual.Count -eq $expected.Count) 'launcher argv count'
    for ($index = 0; $index -lt $expected.Count; $index++) {
        Check ($actual[$index] -ceq $expected[$index]) "launcher argv[$index]"
    }
    $result = Invoke-Native $launcher @('fail7') $base
    Check ($result.ExitCode -eq 7) 'launcher propagates exit code'
    $trust = Read-JsonFile (Join-Path $root 'launcher.json')
    Check ((Get-Field $trust 'root') -ceq $root) 'trusted root keeps exact spelling'
    $embedded = Get-LauncherSource $root
    $root64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($root))
    Check ($embedded.Contains($root64)) 'launcher embeds literal root spelling'
    $launcherAcl = Get-Acl -LiteralPath $launcher
    Check $launcherAcl.AreAccessRulesProtected 'external-bin launcher blocks inherited ACL'
    $fileRules = @($launcherAcl.GetAccessRules($true, $true,
        [Security.Principal.SecurityIdentifier]))
    Check ($fileRules.Count -eq 2) 'launcher has only two access rules'
    foreach ($rule in $fileRules) {
        Check ($rule.IdentityReference -in @($sid,
            [Security.Principal.SecurityIdentifier]'S-1-5-18')) 'launcher private identities'
    }
    # Same valid PE metadata, different actual bytes: no trusted digest match.
    $forged = [IO.File]::ReadAllBytes($launcher) + [byte[]](1, 2, 3)
    [IO.File]::WriteAllBytes($launcher, $forged)
    $forgedHash = (Get-FileHash $launcher).Hash
    [void](Ensure-Launcher $root $bin $base)
    Check ((Get-FileHash $launcher).Hash -cne $forgedHash) 'forged metadata cannot authorize reuse'
    $trust = Read-JsonFile (Join-Path $root 'launcher.json')
    Check ((Get-Field $trust 'binary_sha256') -ceq
        (Get-FileHash $launcher).Hash.ToLowerInvariant()) 'private record verifies binary bytes'
    $realFlags = (Get-Command Get-LauncherCompilerFlags).ScriptBlock
    $oldDigest = Get-Field $trust 'source_sha256'
    try {
        function Get-LauncherCompilerFlags { return ((& $realFlags) + @('/optimize+')) }
        [void](Ensure-Launcher $root $bin $base)
        $changed = Read-JsonFile (Join-Path $root 'launcher.json')
        Check ((Get-Field $changed 'source_sha256') -cne $oldDigest) `
            'compiler flags trigger rebuild'
    } finally { Set-Item function:Get-LauncherCompilerFlags $realFlags }
    [void](Ensure-Launcher $root $bin $base)
    $unchangedBytes = (Get-FileHash $launcher).Hash
    $unchangedTime = [IO.File]::GetLastWriteTimeUtc($launcher)
    $held = [IO.File]::Open($launcher, 'Open', 'Read', 'Read,Delete')
    try {
        Check ((Ensure-Launcher $root $bin $base) -ceq $launcher) 'held launcher reused'
    }
    finally { $held.Dispose() }
    $caseRoot = $root.Replace('root space', 'ROOT SPACE')
    Assert-Launcher $caseRoot $bin
    Check $true 'root identity ignores letter case'
    [void](Ensure-Launcher $caseRoot $bin $base)
    Check ((Get-FileHash $launcher).Hash -ceq $unchangedBytes) 'matching source preserves bytes'
    Check ([IO.File]::GetLastWriteTimeUtc($launcher) -eq $unchangedTime) `
        'matching source skips compile'
    $ready = Join-Path $base 'running-ready'
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $launcher; $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.Arguments = (ConvertTo-NativeArgument $ready) + ' sleep'
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $runningApp = [Diagnostics.Process]::Start($info)
    $appOutput = $runningApp.StandardOutput.ReadToEndAsync()
    $appError = $runningApp.StandardError.ReadToEndAsync()
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        while (-not [IO.File]::Exists($ready) -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 100
        }
        Check ([IO.File]::Exists($ready) -and -not $runningApp.HasExited) 'real old app is running'
        Set-Item function:Get-LauncherSource $realSource
        [void](Ensure-Launcher $root $bin $base)
        Check ((Get-FileHash $launcher).Hash -cne $unchangedBytes) `
            'source upgrade replaces launcher'
        Check (-not $runningApp.HasExited) 'old app survives launcher upgrade'
        $newOutput = Invoke-CheckedNative $launcher @('new-launcher') $base
        Check ($newOutput -ceq '["new-launcher"]') 'new launcher works during old app execution'
        [IO.File]::WriteAllText($ready + '.release', 'continue')
        Check ($runningApp.WaitForExit(20000)) 'old app finishes after upgrade'
        Check ($runningApp.ExitCode -eq 0) ('old app exits cleanly: ' + $appError.Result)
    } catch {
        Write-Output ('Running upgrade failure: ' + ($_ | Out-String))
        throw
    } finally {
        Set-Item function:Get-LauncherSource $realSource
        if (-not $runningApp.HasExited) {
            $cleanup = New-Object Diagnostics.ProcessStartInfo
            $cleanup.FileName = Get-SystemTool 'taskkill.exe'
            $cleanup.Arguments = "/PID $($runningApp.Id) /T /F"
            $cleanup.UseShellExecute = $false; $cleanup.CreateNoWindow = $true
            $killer = [Diagnostics.Process]::Start($cleanup)
            [void]$killer.WaitForExit(10000); $killer.Dispose()
        }
        $runningApp.Dispose()
    }
    $identity = (Get-TextDigest $root).Substring(0, 12)
    $legacy = Join-Path $bin ('.previous-' + [guid]::NewGuid() + '.exe')
    [IO.File]::Copy($launcher, $legacy)
    $pending = Join-Path $bin ('.abs-etl-' + $identity + '-launcher-' +
        [guid]::NewGuid().ToString('N') + '.exe')
    [IO.File]::WriteAllText($pending, 'interrupted compiler output')
    $upgradedBytes = (Get-FileHash $launcher).Hash
    [void](Ensure-Launcher $root $bin $base)
    Check (-not [IO.File]::Exists($legacy)) 'owned legacy backup cleaned'
    Check (-not [IO.File]::Exists($pending)) 'interrupted scoped pending file cleaned'
    Check (@(Get-ChildItem $bin -Force -Filter '.abs-etl-*.exe').Count -eq 0) `
        'old running backup cleaned'
    Check ((Get-FileHash $launcher).Hash -ceq $upgradedBytes) `
        'cleanup does not rebuild matching source'
    # A launcher owned by another account is replaced, never re-permissioned.
    function Get-Acl([string]$LiteralPath) {
        if ($LiteralPath -ne $launcher) {
            return Microsoft.PowerShell.Security\Get-Acl -LiteralPath $LiteralPath
        }
        $foreign = New-Object psobject
        $foreign | Add-Member ScriptMethod GetOwner {
            param($Type) return [Security.Principal.SecurityIdentifier]'S-1-5-11'
        }
        return $foreign
    }
    try { $rebuilt = Ensure-Launcher $root $bin $base 6>&1 | Out-String }
    finally { Remove-Item function:Get-Acl }
    Check ($rebuilt -match 'Building and publishing') 'foreign-owned launcher rebuilt'
    Reject { Ensure-Launcher (Join-Path $base 'different') $bin $base } 'another installation'
    if ($env:ABS_INSTALLER_CASE_PARENT) {
        $caseTest = Join-Path $env:ABS_INSTALLER_CASE_PARENT ('literal-' +
            [guid]::NewGuid().ToString('N').Substring(0, 8))
        try {
            [void][IO.Directory]::CreateDirectory($caseTest); Set-PrivateDirectory $caseTest
            $caseBin = Join-Path $caseTest 'bin'
            [void][IO.Directory]::CreateDirectory($caseBin)
            $caseVenv = Join-Path $caseTest "versions\$slot\source\.venv"
            [void](Invoke-CheckedNative $developmentPython @('-I', '-m', 'venv',
                '--without-pip', $caseVenv) $base)
            [IO.File]::WriteAllText((Join-Path $caseVenv 'Lib\site-packages\abs_census_etl.py'),
                $moduleText)
            Write-AtomicText (Join-Path $caseTest 'active.json') `
                (@{ slot = $slot } | ConvertTo-Json)
            $caseLauncher = Ensure-Launcher $caseTest $caseBin $base
            Check ((Invoke-CheckedNative $caseLauncher @('literal-path') $base) -ceq
                '["literal-path"]') 'launcher works beneath native case-sensitive parent'
        } finally {
            if (Test-Path -LiteralPath $caseTest) { Remove-Item $caseTest -Recurse -Force }
        }
    } else {
        Write-Host 'SKIP: case-sensitive parent probe (set ABS_INSTALLER_CASE_PARENT)'
    }
    Write-AtomicText (Join-Path $root 'active.json') '{"slot":"../escape"}'
    $result = Invoke-Native $launcher @('--help') $base
    Check ($result.ExitCode -eq 1 -and $result.Stderr -match 'Invalid active') 'invalid pointer'
    foreach ($json in @('', '{}', '{"slot":null}')) {
        Write-AtomicText (Join-Path $root 'active.json') $json
        $result = Invoke-Native $launcher @('--help') $base
        Check ($result.ExitCode -eq 1 -and $result.Stderr -match 'rerun the installer') `
            'empty/missing selection recovery hint'
    }
    $saved = @{}
    foreach ($name in @('UV_INDEX_URL', 'PIP_INDEX_URL', 'UV_PROJECT_ENVIRONMENT', 'UV_OVERRIDE',
                       'PYTHONPATH', 'PIP_CERT', 'UV_SYSTEM_CERTS')) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, 'poison', 'Process')
    }
    try {
        $uvContext = [pscustomobject]@{ Root = $root; Work = $base; Uv = $echo; Network = $true }
        $values = Invoke-Uv $uvContext @('-I', '-c', $echoProbe, 'env') $base | ConvertFrom-Json
        foreach ($name in @('UV_INDEX_URL', 'PIP_INDEX_URL', 'UV_PROJECT_ENVIRONMENT',
                           'UV_OVERRIDE', 'PYTHONPATH')) {
            Check ($null -eq $values.$name) "isolated $name"
        }
        Check ($values.SSL_CERT_FILE -ceq 'poison') 'certificate opt-in'
        Check ($values.UV_SYSTEM_CERTS -ceq 'poison') 'system trust opt-in'
        Check ($values.UV_PYTHON_INSTALL_REGISTRY -ceq '0') 'registry install disabled'
        Check ($values.APPDATA -ceq (Join-Path $base 'config\user')) 'user config isolation'
        Check ($values.PROGRAMDATA -ceq (Join-Path $base 'config\system')) 'system config isolation'
        $uvContext.Network = $false
        $defaults = Invoke-Uv $uvContext @('-I', '-c', $echoProbe, 'env') $base | ConvertFrom-Json
        Check ($defaults.UV_SYSTEM_CERTS -ceq 'true') 'Windows system trust enabled by default'
        Check ($defaults.SSL_CERT_FILE -cne 'poison') 'PIP_CERT not admitted without opt-in'
    } finally {
        foreach ($name in $saved.Keys) {
            [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
        }
    }
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Reject { Install-AbsCensus -Prefix (Join-Path $base 'must-not-exist') } 'normal PowerShell'
        # Validation-only cases below still run under admin; never install anything.
        function Assert-StandardUser { }
    }
    foreach ($options in @(@{ Csv = $true; Parquet = $true }, @{ Version = '1!1.0' },
                           @{ ReleaseDir = $inputDir }, @{ Prefix = "C:\bad`n" })) {
        $options.Prefix = $(if ($options.ContainsKey('Prefix')) { $options.Prefix }
                             else { Join-Path $base 'must-not-exist' })
        Reject { Install-AbsCensus @options } 'Choose|version|requires|control'
        Check (-not (Test-Path -LiteralPath (Join-Path $base 'must-not-exist'))) 'early refusal'
    }
    $sharedBin = Join-Path $modify 'bin'
    Reject { Install-AbsCensus -Prefix (Join-Path $base 'must-not-exist') -BinDir $sharedBin } `
        'Other accounts'
    Reject { Install-AbsCensus -Prefix $insideRoot } 'Other accounts'
    Check (-not (Test-Path -LiteralPath (Join-Path $base 'must-not-exist')) -and
        -not (Test-Path -LiteralPath $sharedBin) -and -not (Test-Path -LiteralPath $insideRoot)) `
        'shared locations refused before creating folders'
    $proxyEnv = @{}
    foreach ($name in @('HTTPS_PROXY', 'NO_PROXY')) {
        $proxyEnv[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    try {
        $env:HTTPS_PROXY = 'http://example-user:example-pass@proxy.invalid:8080'
        $env:NO_PROXY = ''
        $proxy = Get-DownloadProxy ([uri]'https://example.org/asset')
        Check ($proxy.Address.UserInfo -eq '') 'proxy URL contains no credentials'
        Check ($proxy.Credentials.UserName -eq 'example-user') 'explicit proxy username'
        Check ($proxy.Credentials.Password -eq 'example-pass') 'explicit proxy password'
        $env:NO_PROXY = '.example.org,other.invalid'
        Check ($null -eq (Get-DownloadProxy ([uri]'https://sub.example.org/x'))) 'NO_PROXY suffix'
        Check ($null -ne (Get-DownloadProxy ([uri]'https://different.org/x'))) 'proxy nonmatch'
        $env:NO_PROXY = ''
        foreach ($address in @('proxy.corp:8080', '10.0.0.1:8080')) {
            $env:HTTPS_PROXY = $address
            $proxy = Get-DownloadProxy ([uri]'https://example.org/x')
            Check ($proxy.Address.Scheme -eq 'http') 'scheme-less proxy defaults to HTTP'
            Check ($proxy.Address.Port -eq 8080) 'scheme-less proxy preserves port'
        }
        $env:NO_PROXY = '*'
        Check ($null -eq (Get-DownloadProxy ([uri]'https://different.org/x'))) 'NO_PROXY wildcard'
    } finally {
        foreach ($name in $proxyEnv.Keys) {
            [Environment]::SetEnvironmentVariable($name, $proxyEnv[$name], 'Process')
        }
    }
    # Real HTTP responses on loopback exercise WebException status handling;
    # no public network or TLS trust changes are needed for these unit checks.
    $serverFile = Join-Path $base 'http-server.py'
    $portFile = Join-Path $base 'http-port'
    $serverCode = @'
from http.server import BaseHTTPRequestHandler, HTTPServer
import pathlib, sys
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = int(self.path[1:])
        self.send_response(status)
        self.send_header('Content-Length', '4')
        self.end_headers()
        self.wfile.write(b'done')
    def log_message(self, *args):
        pass
server = HTTPServer(('127.0.0.1', 0), Handler)
pathlib.Path(sys.argv[1]).write_text(str(server.server_port))
server.serve_forever()
'@
    [IO.File]::WriteAllText($serverFile, $serverCode)
    $serverInfo = New-Object Diagnostics.ProcessStartInfo
    $serverInfo.FileName = $developmentPython; $serverInfo.UseShellExecute = $false
    $serverInfo.CreateNoWindow = $true
    $serverInfo.Arguments = (@('-I', $serverFile, $portFile) |
        ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
    $server = [Diagnostics.Process]::Start($serverInfo)
    $realHttpsOnce = (Get-Command Get-HttpsFileOnce).ScriptBlock
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(20)
        while (-not [IO.File]::Exists($portFile) -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 50
        }
        Check ([IO.File]::Exists($portFile)) 'loopback HTTP server ready'
        $script:TestHttpPort = [IO.File]::ReadAllText($portFile)
        function Get-HttpsFileOnce($Url, $Destination) {
            $status = $script:TestHttpStatuses[[Math]::Min($script:HttpCalls,
                $script:TestHttpStatuses.Count - 1)]
            $script:HttpCalls++
            $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$script:TestHttpPort/$status")
            $request.Proxy = $null
            $response = $request.GetResponse()
            try {
                if ($Destination) {
                    $output = [IO.File]::Create($Destination)
                    try { Copy-DownloadStream $response.GetResponseStream() $output }
                    finally { $output.Dispose() }
                }
                return 'complete'
            } finally { $response.Close() }
        }
        foreach ($status in @(403, 404, 600)) {
            $script:HttpCalls = 0; $script:TestHttpStatuses = @($status)
            Reject { Get-HttpsFile 'https://test.invalid/x' '' } "$status"
            Check ($script:HttpCalls -eq 1) "HTTP $status is not retried"
        }
        $script:HttpCalls = 0; $script:TestHttpStatuses = @(503, 502, 200)
        $downloaded = Join-Path $base 'retried-download'
        Check ((Get-HttpsFile 'https://test.invalid/x' $downloaded) -eq 'complete') `
            '5xx retry succeeds'
        Check ($script:HttpCalls -eq 3) '5xx retries bounded'
        Check ([IO.File]::ReadAllText($downloaded) -eq 'done') 'retried body complete'
        $script:HttpCalls = 0; $script:TestHttpStatuses = @(503)
        Reject { Get-HttpsFile 'https://test.invalid/x' '' } '503'
        Check ($script:HttpCalls -eq 3) 'persistent 5xx stops after three attempts'
        function Get-HttpsFileOnce($Url, $Destination) {
            $script:HttpCalls++
            throw (New-Object Net.WebException('network read',
                (New-Object IO.IOException('partial stream')),
                [Net.WebExceptionStatus]::ReceiveFailure, $null))
        }
        $script:HttpCalls = 0
        Reject { Get-HttpsFile 'https://test.invalid/x' '' } 'network read'
        Check ($script:HttpCalls -eq 3) 'wrapped stream read error retries'
        Check (-not (Test-RetryableDownloadError (New-Object IO.IOException('disk full')))) `
            'local output IO failure is not retried'
        Check (-not (Test-RetryableDownloadError (New-Object Net.WebException('certificate',
            [Net.WebExceptionStatus]::TrustFailure)))) 'TLS trust failures are not retried'
        Add-Type @'
using System;
using System.IO;
public class BrokenDownloadRead : MemoryStream {
    public override int Read(byte[] data, int offset, int count) {
        throw new IOException("remote stream cut off");
    }
}
'@
        $inputStream = New-Object BrokenDownloadRead
        $outputStream = New-Object IO.MemoryStream
        try {
            $streamError = $null
            try { Copy-DownloadStream $inputStream $outputStream }
            catch { $streamError = $_.Exception }
            Check ($null -ne $streamError -and (Test-RetryableDownloadError $streamError)) `
                'interrupted source reads become retryable download errors'
        } finally { $inputStream.Dispose(); $outputStream.Dispose() }
    } finally {
        Set-Item function:Get-HttpsFileOnce $realHttpsOnce
        if (-not $server.HasExited) { $server.Kill() }
        [void]$server.WaitForExit(10000); $server.Dispose()
    }
    $firstRoot = Join-Path $base 'first-install'
    [void][IO.Directory]::CreateDirectory($firstRoot)
    $firstLock = Open-InstallLock (Join-Path $firstRoot '.install-lock')
    try {
        Reject { Install-AbsCensus -Prefix $firstRoot -Version '0.1.0.dev0' } 'lock is busy'
        Check (-not [IO.File]::Exists((Join-Path $firstRoot '.abs-census-etl'))) `
            'competing first install cannot publish marker outside lock'
    } finally { $firstLock.Dispose() }
    $readOnlyLock = Join-Path $base 'read-only-lock'
    [IO.File]::WriteAllText($readOnlyLock, '')
    [IO.File]::SetAttributes($readOnlyLock, [IO.FileAttributes]::ReadOnly)
    try { Reject { Open-InstallLock $readOnlyLock } 'permissions and attributes' }
    finally { [IO.File]::SetAttributes($readOnlyLock, [IO.FileAttributes]::Normal) }
    # Kill the installer host: finally cannot help. The OS must release the
    # lock and terminate both tool and grandchild through the job object.
    Reject { Install-AbsCensus -Prefix ('C:\' + 'x' * 101) } 'shorter|exceeds'
    $hostScript = Join-Path $base 'crash-host.ps1'
    $childScript = Join-Path $base 'sleep-child.ps1'
    $pidFile = Join-Path $base 'descendants.txt'
    $crashLock = Join-Path $base 'crash-lock'
    $installer = Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1'
    $childCode = @'
param($PidFile)
$exe = Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe'
$grandchild = Start-Process -FilePath $exe -ArgumentList @(
    '-NoProfile', '-Command', 'Start-Sleep -Seconds 120') -WindowStyle Hidden -PassThru
[IO.File]::WriteAllText($PidFile, "$PID`n$($grandchild.Id)")
Start-Sleep -Seconds 120
'@
    $hostCode = @'
param($Installer, $Work, $ChildScript, $PidFile, $LockPath)
. $Installer
$handle = Open-InstallLock $LockPath
try {
    Invoke-Native (Get-SystemTool 'WindowsPowerShell\v1.0\powershell.exe') @(
        '-NoProfile', '-File', $ChildScript, $PidFile) $Work
} finally { $handle.Dispose() }
'@
    [IO.File]::WriteAllText($hostScript, $hostCode)
    [IO.File]::WriteAllText($childScript, $childCode)
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = Get-SystemTool 'WindowsPowerShell\v1.0\powershell.exe'
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.Arguments = (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $hostScript,
        $installer, $base, $childScript, $pidFile, $crashLock) |
        ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
    $hostProcess = [Diagnostics.Process]::Start($info)
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while (-not [IO.File]::Exists($pidFile) -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 100
        }
        Check ([IO.File]::Exists($pidFile)) 'tool and grandchild started'
        $descendants = @([IO.File]::ReadAllLines($pidFile) | ForEach-Object { [int]$_ })
        $hostProcess.Kill(); [void]$hostProcess.WaitForExit(10000)
        Start-Sleep -Milliseconds 500
        foreach ($childId in $descendants) {
            Check (Test-ProcessStopped $childId) `
                'job closes descendant on host crash'
        }
        Check (-not [IO.File]::Exists($crashLock)) 'crash releases lock'
        $handle = Open-InstallLock $crashLock; $handle.Dispose()
        Check (-not [IO.File]::Exists($crashLock)) 'next run reacquires lock'
    } finally {
        if (-not $hostProcess.HasExited) {
            $killer = [Diagnostics.Process]::Start((Get-SystemTool 'taskkill.exe'),
                "/PID $($hostProcess.Id) /T /F")
            [void]$killer.WaitForExit(10000); $killer.Dispose()
        }
        $hostProcess.Dispose()
    }
    Remove-Item -LiteralPath $pidFile
    $pipeline = [PowerShell]::Create()
    try {
        [void]$pipeline.AddScript($hostCode).AddArgument($installer).AddArgument($base).
            AddArgument($childScript).AddArgument($pidFile).AddArgument($crashLock)
        $running = $pipeline.BeginInvoke()
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while (-not [IO.File]::Exists($pidFile) -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 100
        }
        Check ([IO.File]::Exists($pidFile)) 'cancellable tool and grandchild started'
        $descendants = @([IO.File]::ReadAllLines($pidFile) | ForEach-Object { [int]$_ })
        # Stopping a pipeline exercises the same unwinding used for Ctrl+C.
        $stopwatch = [Diagnostics.Stopwatch]::StartNew()
        $pipeline.Stop()
        Check ($stopwatch.Elapsed.TotalSeconds -lt 10) 'cancellation completes promptly'
        try { [void]$pipeline.EndInvoke($running) } catch { }
        foreach ($childId in $descendants) {
            Check (Test-ProcessStopped $childId) `
                'pipeline cancellation terminates descendant'
        }
        Check (-not [IO.File]::Exists($crashLock)) 'cancellation releases lock'
    } finally { $pipeline.Dispose() }
    Write-Host "PASS: $script:Checks offline Windows installer assertions"
} finally { Remove-Item -LiteralPath $base -Recurse -Force }
