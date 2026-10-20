#Requires -Version 5.1
<# User-local GitHub release installation/update. No Python, Git or elevation needed. #>
[CmdletBinding()]
param(
    [switch]$Csv,
    [switch]$Parquet,
    [string]$Version,
    [string]$Prefix,
    [string]$BinDir,
    [string]$Repository,
    [string]$ReleaseDir,
    [string]$Uv,
    [switch]$UseNetworkEnv,
    [switch]$Help
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:UvVersion = '0.12.21'
$script:UvSha256 = '5d223efa0bf00208c3853246af09420419dfbd352536aa6bb8163d6170e23890'

function Test-ReleaseVersion([string]$Value) {
    $n = '(0|[1-9][0-9]*)'
    return $Value -cmatch ('\A' + $n + '(\.' + $n + ')*' +
        '((a|b|rc)' + $n + ')?(\.post' + $n + ')?(\.dev' + $n + ')?\z')
}

function Get-Field($Value, [string]$Name) {
    if ($null -eq $Value) { return $null }
    $property = $Value.PSObject.Properties[$Name]
    if ($null -ne $property) { return $property.Value }
    return $null
}

function Assert-Repository([string]$Value) {
    if ($Value -cnotmatch '\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\z') {
        throw 'Repository must be OWNER/REPO using portable ASCII components.'
    }
}

function Assert-StandardUser {
    $principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this installer in a normal PowerShell window, not as administrator.'
    }
}

function Get-SystemTool([string]$Name) {
    $system = if ([Environment]::Is64BitProcess) { 'System32' } else { 'Sysnative' }
    $path = Join-Path $env:WINDIR "$system\$Name"
    if (-not [IO.File]::Exists($path)) { throw "Required Windows tool is missing: $path" }
    return $path
}

function Get-NativeArchitecture {
    if (-not ('AbsNativeArchitecture' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class AbsNativeArchitecture {
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool IsWow64Process2(IntPtr process, out ushort guest, out ushort native);
    public static ushort Get() {
        ushort guest, native;
        if (!IsWow64Process2(System.Diagnostics.Process.GetCurrentProcess().Handle,
                             out guest, out native)) throw new Win32Exception();
        return native;
    }
}
'@
    }
    try { return [AbsNativeArchitecture]::Get() }
    catch {
        $cause = $_.Exception
        while ($cause.InnerException) { $cause = $cause.InnerException }
        if ($cause -is [EntryPointNotFoundException]) {
            throw 'Windows 10 version 1803+ with the OS-supplied tar.exe is required.'
        }
        throw
    }
}

function Get-ManagedTarget {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Windows is required.' }
    $os = [Environment]::OSVersion.Version
    if ($os.Major -lt 10 -or ($os.Major -eq 10 -and $os.Build -lt 17134)) {
        throw 'Windows 10 version 1803+ with the OS-supplied tar.exe is required.'
    }
    if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
    # Query the native machine even from a 32-bit/emulated shell, without WMI.
    if ((Get-NativeArchitecture) -ne 0x8664) {
        throw 'This installer requires native x64 Windows; x86 and ARM64 are unsupported.'
    }
    return 'x86_64-pc-windows-msvc'
}

function Get-InstallPath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match '[\x00-\x1f]') {
        throw 'Paths must not be blank or contain control characters.'
    }
    if ($Value -notmatch '\A[A-Za-z]:[\\/]') {
        throw 'Use an absolute local drive path, not a relative, UNC or device path.'
    }
    foreach ($part in ($Value.Substring(3) -split '[\\/]')) {
        if ($part -in @('.', '..') -or $part -match '[ .]$|[<>:"|?*]' -or
            $part -match '\A(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)') {
            throw 'Use normal Windows path components without aliases or reserved names.'
        }
    }
    $path = [IO.Path]::GetFullPath($Value).TrimEnd('\', '/')
    if ($path.Length -le 2) { throw 'Refusing a drive root as an installation directory.' }
    $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($path))
    if ($drive.DriveType -eq [IO.DriveType]::Network) {
        throw 'Mapped network drives are unsupported; choose a local installation path.'
    }
    if (-not $drive.IsReady) { throw 'The selected local drive is unavailable.' }
    return $path
}

function Assert-PlainPath([string]$Path) {
    $current = $Path
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Reparse points are not supported in managed paths: $current"
            }
            if ($current -ne $Path -and -not $item.PSIsContainer) {
                throw "An ancestor is not a directory: $current"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

function Assert-OwnedDirectory([string]$Path) {
    Assert-PlainPath $Path
    if (Test-Path -LiteralPath $Path) {
        if (-not (Get-Item -LiteralPath $Path -Force).PSIsContainer) {
            throw "Not a directory: $Path"
        }
        $owner = (Get-Acl -LiteralPath $Path).GetOwner([Security.Principal.SecurityIdentifier])
        if ($owner -ne [Security.Principal.WindowsIdentity]::GetCurrent().User) {
            throw "Directory must be owned by your account: $Path"
        }
    }
}

function Assert-PlainFile([string]$Path) {
    Assert-PlainPath $Path
    if ((Test-Path -LiteralPath $Path) -and (Get-Item -LiteralPath $Path -Force).PSIsContainer) {
        throw "Not a regular file: $Path"
    }
}

function New-PrivateDirectorySecurity {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($identity in @($sid, [Security.Principal.SecurityIdentifier]'S-1-5-18')) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    return $acl
}

function Set-PrivateDirectory([string]$Path) {
    $acl = New-PrivateDirectorySecurity
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $current = Get-Acl -LiteralPath $Path
    $rules = @($current.GetAccessRules($true, $true,
        [Security.Principal.SecurityIdentifier]))
    $aclMatches = $current.AreAccessRulesProtected -and $rules.Count -eq 2 -and
        $current.GetOwner([Security.Principal.SecurityIdentifier]) -eq $sid
    foreach ($identity in @($sid, [Security.Principal.SecurityIdentifier]'S-1-5-18')) {
        $expected = @($rules | Where-Object {
            $_.IdentityReference -eq $identity -and -not $_.IsInherited -and
            $_.AccessControlType -eq 'Allow' -and $_.FileSystemRights -eq 'FullControl' -and
            $_.InheritanceFlags -eq 'ContainerInherit,ObjectInherit' -and
            $_.PropagationFlags -eq 'None'
        })
        $aclMatches = $aclMatches -and $expected.Count -eq 1
    }
    if (-not $aclMatches) { Set-Acl -LiteralPath $Path -AclObject $acl }
}

function New-PrivateDirectory([string]$Path) {
    # Missing components are created with the private DACL already applied, so
    # no other account gets a window to open or populate them first.
    $missing = @()
    $current = $Path
    while (-not [IO.Directory]::Exists($current)) {
        $missing = @($current) + $missing
        $current = [IO.Path]::GetDirectoryName($current)
        if (-not $current) { throw "No existing parent directory for $Path" }
    }
    if (-not $missing.Count) { return }
    if (-not ('AbsPrivateDirectory' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class AbsPrivateDirectory {
    [StructLayout(LayoutKind.Sequential)] struct SecurityAttributes {
        public int Length; public IntPtr Descriptor; public bool Inherit;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool CreateDirectoryW(string path, ref SecurityAttributes attributes);
    public static void Create(string path, byte[] descriptor) {
        GCHandle pinned = GCHandle.Alloc(descriptor, GCHandleType.Pinned);
        try {
            var attributes = new SecurityAttributes();
            attributes.Length = Marshal.SizeOf(attributes);
            attributes.Descriptor = pinned.AddrOfPinnedObject();
            if (!CreateDirectoryW(path, ref attributes)) throw new Win32Exception();
        } finally { pinned.Free(); }
    }
}
'@
    }
    $descriptor = (New-PrivateDirectorySecurity).GetSecurityDescriptorBinaryForm()
    foreach ($directory in $missing) {
        try { [AbsPrivateDirectory]::Create($directory, $descriptor) }
        catch {
            # A concurrent run of this account may win; ownership is checked below.
            if (-not [IO.Directory]::Exists($directory)) { throw }
        }
        Assert-OwnedDirectory $directory
    }
}

function Assert-ProtectedLocation([string]$Path, [switch]$Contents, [string]$Stop) {
    # Another account that can rename or re-permission this folder or a folder
    # above it, or add files beside the launcher, could substitute what this
    # account later runs. Missing components are created private later.
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $trusted = @($user.Value, 'S-1-5-18', 'S-1-5-32-544', 'S-1-3-0', 'S-1-3-4',
        'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
    for ($current = $Path; $current; $current = [IO.Path]::GetDirectoryName($current)) {
        if ($Stop -and [string]::Equals($current, $Stop, [StringComparison]::OrdinalIgnoreCase)) {
            break
        }
        if (-not [IO.Directory]::Exists($current)) { continue }
        try { $acl = Get-Acl -LiteralPath $current }
        catch { throw "Cannot verify permissions of $current. Choose a folder in your profile." }
        $target = $Contents -and $current -eq $Path
        # Delete child, change permissions, take ownership, generic all.
        $unsafe = 0x40 -bor 0x40000 -bor 0x80000 -bor 0x10000000
        if ([IO.Path]::GetPathRoot($current) -ne $current) { $unsafe = $unsafe -bor 0x10000 }
        # Add files/folders or write files created inside the launcher folder.
        if ($target) { $unsafe = $unsafe -bor 0x2 -bor 0x4 -bor 0x40000000 }
        $exposed = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted
        foreach ($rule in $acl.GetAccessRules($true, $true,
                [Security.Principal.SecurityIdentifier])) {
            $inheritOnly = $rule.PropagationFlags -band
                [Security.AccessControl.PropagationFlags]::InheritOnly
            if ($rule.AccessControlType -ne 'Allow' -or
                $rule.IdentityReference.Value -in $trusted -or ($inheritOnly -and -not $target)) {
                continue
            }
            if ([int]$rule.FileSystemRights -band $unsafe) { $exposed = $true }
        }
        if ($exposed) {
            throw "Other accounts can modify or replace $current. " +
                'Choose a folder only your account controls, such as one in %LOCALAPPDATA%.'
        }
    }
}

function Set-PrivateFile([string]$Path) {
    $acl = New-Object Security.AccessControl.FileSecurity
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.SetOwner($sid)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($identity in @($sid, [Security.Principal.SecurityIdentifier]'S-1-5-18')) {
        $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
            $identity, 'FullControl', 'Allow')))
    }
    $current = Get-Acl -LiteralPath $Path
    $rules = @($current.GetAccessRules($true, $true,
        [Security.Principal.SecurityIdentifier]))
    $aclMatches = $current.AreAccessRulesProtected -and $rules.Count -eq 2 -and
        $current.GetOwner([Security.Principal.SecurityIdentifier]) -eq $sid
    foreach ($identity in @($sid, [Security.Principal.SecurityIdentifier]'S-1-5-18')) {
        $expected = @($rules | Where-Object {
            $_.IdentityReference -eq $identity -and -not $_.IsInherited -and
            $_.AccessControlType -eq 'Allow' -and $_.FileSystemRights -eq 'FullControl' -and
            $_.InheritanceFlags -eq 'None' -and $_.PropagationFlags -eq 'None'
        })
        $aclMatches = $aclMatches -and $expected.Count -eq 1
    }
    if (-not $aclMatches) { Set-Acl -LiteralPath $Path -AclObject $acl }
}

function Invoke-SharingRetry([scriptblock]$Action) {
    for ($attempt = 0; ; $attempt++) {
        try { & $Action; return }
        catch {
            $errorObject = $_.Exception
            while ($errorObject.InnerException) { $errorObject = $errorObject.InnerException }
            if (($errorObject.HResult -band 0xffff) -notin @(32, 33) -or $attempt -ge 8) { throw }
            Start-Sleep -Milliseconds 250
        }
    }
}

function Open-InstallLock([string]$Path) {
    Assert-PlainFile $Path
    try {
        return New-Object IO.FileStream($Path, [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite, [IO.FileShare]::None, 4096,
            [IO.FileOptions]::DeleteOnClose)
    } catch {
        $cause = $_.Exception
        while ($cause.InnerException) { $cause = $cause.InnerException }
        $code = $cause.HResult -band 0xffff
        if ($code -in @(32, 33, 303)) {
            throw "Installation lock is busy or being released: $Path. Retry shortly."
        }
        throw "Cannot open installation lock: $Path. Check its permissions and attributes."
    }
}

function Write-AtomicText([string]$Path, [string]$Text) {
    Assert-PlainFile $Path
    $temporary = Join-Path ([IO.Path]::GetDirectoryName($Path)) ('.part-' + [guid]::NewGuid())
    try {
        [IO.File]::WriteAllText($temporary, $Text, (New-Object Text.UTF8Encoding($false)))
        Invoke-SharingRetry {
            if ([IO.File]::Exists($Path)) {
                [IO.File]::Replace($temporary, $Path, [NullString]::Value)
            } else { [IO.File]::Move($temporary, $Path) }
        }
    } finally {
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
}

function Read-JsonFile([string]$Path) {
    Assert-PlainFile $Path
    if (-not [IO.File]::Exists($Path)) { return $null }
    try { return ([IO.File]::ReadAllText($Path) | ConvertFrom-Json) }
    catch { return $null }
}

function Test-Selection($Value) {
    return ((Get-Field $Value 'slot') -cmatch '\Arelease-([0-9a-f]{12}|[0-9a-f]{32})\z' -and
        (Get-Field $Value 'format') -cin @('csv', 'parquet') -and
        (Get-Field $Value 'repository') -cmatch '\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\z' -and
        -not [string]::IsNullOrWhiteSpace((Get-Field $Value 'bin_dir')))
}

function ConvertTo-NativeArgument([string]$Value) {
    # Microsoft C runtime argv quoting, also supported by CPython and uv.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function New-ChildJob {
    if (-not ('AbsInstallerJob' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class AbsInstallerJob {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr CreateJobObject(IntPtr security, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool SetInformationJobObject(IntPtr job, int kind, IntPtr info, uint size);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
    [StructLayout(LayoutKind.Sequential)] struct Basic {
        public long ProcessTime, JobTime; public uint Flags;
        public UIntPtr MinWorkingSet, MaxWorkingSet; public uint ActiveProcessLimit;
        public UIntPtr Affinity; public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)] struct Counters {
        public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
    }
    [StructLayout(LayoutKind.Sequential)] struct Extended {
        public Basic Limits; public Counters Io;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    public static IntPtr Create() {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception();
        var info = new Extended(); info.Limits.Flags = 0x2000; // KILL_ON_JOB_CLOSE
        int size = Marshal.SizeOf(info); IntPtr memory = Marshal.AllocHGlobal(size);
        try {
            Marshal.StructureToPtr(info, memory, false);
            if (!SetInformationJobObject(job, 9, memory, (uint)size))
                throw new Win32Exception();
            return job;
        } catch { CloseHandle(job); throw; }
        finally { Marshal.FreeHGlobal(memory); }
    }
    public static void Assign(IntPtr job, IntPtr process) {
        if (!AssignProcessToJobObject(job, process)) throw new Win32Exception();
    }
}
'@
    }
    return [AbsInstallerJob]::Create()
}

function Invoke-Native {
    param([string]$File, [string[]]$Arguments, [string]$Directory,
          [hashtable]$Environment = @{}, [switch]$Isolated, [string]$InputText)
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $File
    $info.Arguments = ($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
    $info.WorkingDirectory = $Directory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.RedirectStandardInput = $true
    $info.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $info.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
    if ($Isolated) {
        foreach ($name in @($info.EnvironmentVariables.Keys)) {
            if ($name -match '\A(UV_|PIP_)' -or
                $name -in @('VIRTUAL_ENV', 'PYTHONHOME', 'PYTHONPATH')) {
                $info.EnvironmentVariables.Remove($name)
            }
        }
    }
    foreach ($name in $Environment.Keys) { $info.EnvironmentVariables[$name] = $Environment[$name] }
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $started = $false
    $job = New-ChildJob
    try {
        [void]$process.Start()
        $started = $true
        # Assignment follows Start: a very early child spawn can precede job
        # membership. The tested crash/cancel boundary is after assignment.
        [AbsInstallerJob]::Assign($job, $process.Handle)
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if ($InputText) { $process.StandardInput.Write($InputText) }
        $process.StandardInput.Close()
        while (-not $process.WaitForExit(250)) { Start-Sleep -Milliseconds 1 }
        return [pscustomobject]@{ ExitCode = $process.ExitCode
            Stdout = $stdout.GetAwaiter().GetResult(); Stderr = $stderr.GetAwaiter().GetResult() }
    } finally {
        try {
            if ($started -and -not $process.HasExited) {
                $killInfo = New-Object Diagnostics.ProcessStartInfo
                $killInfo.FileName = Get-SystemTool 'taskkill.exe'
                $killInfo.Arguments = "/PID $($process.Id) /T /F"
                $killInfo.UseShellExecute = $false; $killInfo.CreateNoWindow = $true
                $killInfo.RedirectStandardOutput = $true
                $killInfo.RedirectStandardError = $true
                $killer = [Diagnostics.Process]::Start($killInfo)
                try {
                    $killOut = $killer.StandardOutput.ReadToEndAsync()
                    $killError = $killer.StandardError.ReadToEndAsync()
                    [void]$killer.WaitForExit(10000)
                } finally { $killer.Dispose() }
            }
        } catch { Write-Warning 'Child cleanup failed; closing its process job.' }
        finally {
            [void][AbsInstallerJob]::CloseHandle($job)
            $process.Dispose()
        }
    }
}

function Invoke-CheckedNative {
    param([string]$File, [string[]]$Arguments, [string]$Directory,
          [hashtable]$Environment = @{}, [switch]$Isolated, [string]$InputText)
    $result = Invoke-Native @PSBoundParameters
    if ($result.ExitCode -ne 0) {
        throw "Command failed (exit $($result.ExitCode)): $File`n$($result.Stdout)$($result.Stderr)"
    }
    return $result.Stdout.TrimEnd("`r", "`n")
}

function Invoke-Uv($Context, [string[]]$Arguments, [string]$Directory) {
    $environment = @{
        UV_PYTHON_INSTALL_REGISTRY = '0'; UV_SYSTEM_CERTS = 'true'
        UV_PYTHON_INSTALL_DIR = (Join-Path $Context.Root 'python')
        UV_CACHE_DIR = (Join-Path $Context.Root 'cache\uv'); UV_PYTHON_INSTALL_BIN = 'false'
        APPDATA = (Join-Path $Context.Work 'config\user')
        PROGRAMDATA = (Join-Path $Context.Work 'config\system')
        XDG_CONFIG_HOME = (Join-Path $Context.Work 'config\user')
        XDG_CONFIG_DIRS = (Join-Path $Context.Work 'config\system')
    }
    if ($Context.Network) {
        if ($env:PIP_CERT) { $environment.SSL_CERT_FILE = $env:PIP_CERT }
        if ($env:UV_SYSTEM_CERTS) { $environment.UV_SYSTEM_CERTS = $env:UV_SYSTEM_CERTS }
    }
    return Invoke-CheckedNative $Context.Uv $Arguments $Directory $environment -Isolated
}

function Get-DownloadProxy([uri]$Uri) {
    $proxy = [Net.WebRequest]::GetSystemWebProxy()
    $proxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials
    if ($env:HTTPS_PROXY) {
        $proxyAddress = $env:HTTPS_PROXY
        if ($proxyAddress -notmatch '://') { $proxyAddress = 'http://' + $proxyAddress }
        $proxyUri = [uri]$proxyAddress
        $builder = New-Object UriBuilder($proxyUri)
        $builder.UserName = ''; $builder.Password = ''
        $proxy = New-Object Net.WebProxy($builder.Uri)
        $proxy.Credentials = [Net.CredentialCache]::DefaultNetworkCredentials
        if ($proxyUri.UserInfo) {
            $credentials = $proxyUri.UserInfo -split ':', 2
            $password = if ($credentials.Count -gt 1) { $credentials[1] } else { '' }
            $proxy.Credentials = New-Object Net.NetworkCredential(
                [uri]::UnescapeDataString($credentials[0]),
                [uri]::UnescapeDataString($password))
        }
    }
    foreach ($entry in ($env:NO_PROXY -split ',')) {
        $entry = $entry.Trim().TrimStart('.')
        if ($entry -and ($entry -eq '*' -or $Uri.Host -ieq $entry -or
            $Uri.Host.EndsWith('.' + $entry, [StringComparison]::OrdinalIgnoreCase))) {
            return $null
        }
    }
    return $proxy
}

function Test-RetryableDownloadError([Exception]$ErrorObject) {
    while ($ErrorObject -isnot [Net.WebException] -and $ErrorObject.InnerException) {
        $ErrorObject = $ErrorObject.InnerException
    }
    if ($ErrorObject -is [Net.WebException]) {
        if ($ErrorObject.Response -is [Net.HttpWebResponse]) {
            $status = [int]$ErrorObject.Response.StatusCode
            return $status -ge 500 -and $status -lt 600
        }
        return $ErrorObject.Status -in @('ConnectFailure', 'ConnectionClosed',
            'KeepAliveFailure', 'NameResolutionFailure', 'ProxyNameResolutionFailure',
            'ReceiveFailure', 'SendFailure', 'Timeout', 'PipelineFailure')
    }
    return $false
}

function Get-HttpsFile([string]$Url, [string]$Destination) {
    for ($attempt = 0; ; $attempt++) {
        try { return Get-HttpsFileOnce $Url $Destination }
        catch {
            $failure = $_.Exception
            while ($failure -isnot [Net.WebException] -and $failure.InnerException) {
                $failure = $failure.InnerException
            }
            # CoreCLR clears HttpWebResponse.StatusCode on Close. Decide first,
            # then release the failed response on both retry and refusal paths.
            try { $retryable = Test-RetryableDownloadError $failure }
            finally {
                if ($failure -is [Net.WebException] -and $failure.Response) {
                    $failure.Response.Close()
                }
            }
            if ($attempt -ge 2 -or -not $retryable) { throw }
            Write-Host 'Transient download failure; retrying...'
            Start-Sleep -Seconds 1
        }
    }
}

function Copy-DownloadStream([IO.Stream]$Source, [IO.Stream]$Destination) {
    $buffer = New-Object byte[] 65536
    while ($true) {
        # Distinguish a remote stream read failure from a local disk write failure.
        try { $count = $Source.Read($buffer, 0, $buffer.Length) }
        catch [IO.IOException] {
            throw (New-Object Net.WebException('Download stream was interrupted.',
                $_.Exception, [Net.WebExceptionStatus]::ReceiveFailure, $null))
        }
        if ($count -eq 0) { return }
        $Destination.Write($buffer, 0, $count)
    }
}

function Get-HttpsFileOnce([string]$Url, [string]$Destination) {
    $originalProtocol = [Net.ServicePointManager]::SecurityProtocol
    try {
        [Net.ServicePointManager]::SecurityProtocol = $originalProtocol -bor 3072 # TLS 1.2
        for ($redirect = 0; $redirect -lt 10; $redirect++) {
            $uri = [uri]$Url
            if ($uri.Scheme -ne 'https') { throw 'Only HTTPS downloads/redirects are allowed.' }
            $request = [Net.HttpWebRequest]::Create($uri)
            $request.AllowAutoRedirect = $false
            $request.Timeout = 300000
            $request.UserAgent = 'abs-census-etl-installer'
            $request.Proxy = Get-DownloadProxy $uri
            $response = $request.GetResponse()
            try {
                if ([int]$response.StatusCode -in @(301, 302, 303, 307, 308)) {
                    $Url = (New-Object uri($uri, $response.Headers['Location'])).AbsoluteUri
                    continue
                }
                if ($Destination) {
                    $output = [IO.File]::Create($Destination)
                    try { Copy-DownloadStream $response.GetResponseStream() $output }
                    finally { $output.Dispose() }
                }
                return $uri.AbsoluteUri
            } finally { $response.Close() }
        }
        throw 'Too many HTTPS redirects.'
    } finally { [Net.ServicePointManager]::SecurityProtocol = $originalProtocol }
}

function Resolve-Release([string]$Repository) {
    $effective = Get-HttpsFile "https://github.com/$Repository/releases/latest" ''
    if ($effective -cnotmatch '\Ahttps://github.com/([^/]+/[^/]+)/releases/tag/v([^/?#]+)\z') {
        throw 'Latest did not resolve to a versioned release tag.'
    }
    $repo = $Matches[1]; $version = $Matches[2]
    Assert-Repository $repo
    if (-not (Test-ReleaseVersion $version)) { throw 'Latest resolved to an invalid version.' }
    if ($repo -cne $Repository) { Write-Host "Latest release redirects to repository: $repo" }
    return [pscustomobject]@{ Repository = $repo; Version = $version }
}

function Get-VerifiedAsset($Context, [string]$Name) {
    $entries = @([IO.File]::ReadAllLines((Join-Path $Context.Work 'SHA256SUMS')) |
        Where-Object { ($_ -split '\s+')[-1] -ceq $Name })
    if ($entries.Count -ne 1 -or
        $entries[0] -cnotmatch ('\A([0-9a-f]{64})  ' + [regex]::Escape($Name) + '\z')) {
        throw "Missing, duplicate or invalid SHA256SUMS entry: $Name"
    }
    $expected = $Matches[1]
    $path = Join-Path $Context.Work $Name
    if ($Context.ReleaseDir) { [IO.File]::Copy((Join-Path $Context.ReleaseDir $Name), $path) }
    else { [void](Get-HttpsFile "$($Context.AssetUrl)/$Name" $path) }
    $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -cne $expected) {
        throw "Release asset checksum mismatch: $Name"
    }
    return $path
}

function Assert-SourceArchive([string]$Archive, [string]$Version, [string]$Directory) {
    $tar = (Get-SystemTool 'tar.exe')
    $members = Invoke-CheckedNative $tar @('-tzf', $Archive) $Directory
    $types = Invoke-CheckedNative $tar @('-tvzf', $Archive) $Directory
    $root = 'abs_census_etl-' + $Version
    foreach ($name in ($members -split '\r?\n')) {
        $name = $name.TrimEnd('/')
        $inRoot = $name -ceq $root -or $name.StartsWith($root + '/', [StringComparison]::Ordinal)
        if (-not $inRoot -or $name -cmatch '[^A-Za-z0-9_./-]|(^|/)\.{1,2}(/|$)|//') {
            throw "Unsafe archive path: $name"
        }
    }
    foreach ($line in ($types -split '\r?\n')) {
        if ($line -cnotmatch '\A[-d]') { throw 'Archive links/special files are refused.' }
    }
}

function Expand-UvZip([string]$Archive, [string]$Directory) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName -cnotin @('uv.exe', 'uvx.exe', 'uvw.exe')) {
                throw "Unexpected bootstrap archive member: $($entry.FullName)"
            }
            if (($entry.ExternalAttributes -band 0xF0000000L) -eq 0xA0000000L) {
                throw 'Bootstrap archive links are refused.'
            }
        }
        $uv = @($zip.Entries | Where-Object { $_.FullName -ceq 'uv.exe' })
        if ($uv.Count -ne 1) { throw 'Bootstrap archive must contain exactly one uv.exe.' }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($uv[0], (Join-Path $Directory 'uv.exe'))
    } finally { $zip.Dispose() }
}

function Test-Environment([string]$Directory, [string]$Version, [string]$Format) {
    $python = Join-Path $Directory 'source\.venv\Scripts\python.exe'
    if (-not [IO.File]::Exists($python)) { return $false }
    $probe = @'
import importlib.metadata, sys
import abs_census_etl
if importlib.metadata.version('abs-census-etl') != sys.argv[1]:
    raise SystemExit('Installed version differs from selected release')
if sys.argv[2] == 'parquet':
    import pyarrow
'@
    try {
        [void](Invoke-CheckedNative $python @('-I', '-X', 'utf8', '-', $Version, $Format) `
            $Directory -Isolated -InputText $probe)
        [void](Invoke-CheckedNative $python @('-I', '-X', 'utf8', '-m', 'abs_census_etl',
            '--help') $Directory -Isolated)
        return $true
    } catch { return $false }
}

function Get-LauncherSource([string]$Root) {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Root))
    return @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
[assembly: AssemblyTitle("abs-census-etl managed launcher v1")]
[assembly: AssemblyCompany("ABSROOT:ROOT_BASE64")]
[assembly: AssemblyInformationalVersion("ABSLAUNCHER:LAUNCHER_DIGEST")]
public class Launcher {
    static string Quote(string value) {
        string escaped = Regex.Replace(value, "(\\\\*)\"", "$1$1\\\"");
        escaped = Regex.Replace(escaped, "(\\\\+)$", "$1$1");
        return "\"" + escaped + "\"";
    }
    static string ReadSelection(string path) {
        using (var file = new FileStream(path, FileMode.Open, FileAccess.Read,
                                        FileShare.ReadWrite | FileShare.Delete))
        using (var reader = new StreamReader(file)) return reader.ReadToEnd();
    }
    public static int Main(string[] args) {
        try {
            string root = Encoding.UTF8.GetString(Convert.FromBase64String("ROOT_BASE64"));
            string pointer = Path.Combine(root, "active.json");
            if ((File.GetAttributes(pointer) & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Refusing a linked active pointer");
            var selection = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(
                ReadSelection(pointer));
            object value;
            if (selection == null || !selection.TryGetValue("slot", out value) ||
                !(value is string) ||
                !Regex.IsMatch((string)value, @"\Arelease-([0-9a-f]{12}|[0-9a-f]{32})\z"))
                throw new IOException("Invalid active selection; rerun the installer");
            string slot = (string)value;
            string python = Path.Combine(root, "versions", slot, "source", ".venv",
                "Scripts", "python.exe");
            var command = new List<string> { "-I", "-X", "utf8", "-m", "abs_census_etl" };
            command.AddRange(args);
            var info = new ProcessStartInfo(python);
            info.UseShellExecute = false;
            info.Arguments = String.Join(" ", command.ConvertAll(Quote).ToArray());
            Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs e) {
                e.Cancel = true;
            };
            using (var child = Process.Start(info)) { child.WaitForExit(); return child.ExitCode; }
        } catch (Exception error) {
            Console.Error.WriteLine("ERROR: " + error.Message); return 1;
        }
    }
}
'@.Replace('ROOT_BASE64', $encoded)
}

function Assert-Launcher([string]$Root, [string]$Bin) {
    $path = Join-Path $Bin 'abs-census-etl.exe'
    Assert-PlainFile $path
    if ([IO.File]::Exists($path)) {
        $metadata = [Diagnostics.FileVersionInfo]::GetVersionInfo($path)
        try {
            if ($metadata.FileDescription -cne 'abs-census-etl managed launcher v1' -or
                -not $metadata.CompanyName.StartsWith('ABSROOT:')) { throw 'Unknown launcher' }
            $recorded = [Text.Encoding]::UTF8.GetString(
                [Convert]::FromBase64String($metadata.CompanyName.Substring(8)))
            if (-not [string]::Equals($recorded, $Root, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Different installation root'
            }
        } catch {
            throw "Launcher belongs to another installation or is damaged: $path. " +
                'Choose a fresh -BinDir, or move this file aside after checking it.'
        }
    }
}

function Get-TextDigest([string]$Text) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash(
            [Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function Remove-StaleLaunchers([string]$Root, [string]$Bin, [string]$Identity) {
    foreach ($file in Get-ChildItem -LiteralPath $Bin -File -Force) {
        $scoped = $file.Name -cmatch ('\A\.abs-etl-' + $Identity +
            '-(launcher|previous)-[0-9a-f]{32}\.exe\z')
        $legacy = $file.Name -cmatch '\A\.(launcher|previous)-[0-9a-f-]{36}\.exe\z'
        if (-not $scoped -and -not $legacy) { continue }
        Assert-PlainFile $file.FullName
        if (-not $scoped) {
            # Legacy generic names are deletable only with our root ownership marker.
            try {
                $metadata = [Diagnostics.FileVersionInfo]::GetVersionInfo($file.FullName)
                $encodedRoot = $metadata.CompanyName.Substring(8)
                $bytes = [Convert]::FromBase64String($encodedRoot)
                $recorded = [Text.Encoding]::UTF8.GetString($bytes)
                if ($metadata.FileDescription -cne 'abs-census-etl managed launcher v1' -or
                    -not $metadata.CompanyName.StartsWith('ABSROOT:') -or
                    -not [string]::Equals($recorded, $Root,
                        [StringComparison]::OrdinalIgnoreCase)) { continue }
            } catch { continue }
        }
        try { [IO.File]::Delete($file.FullName) }
        catch { Write-Warning "Temporary launcher is still in use: $($file.FullName)" }
    }
}

function Get-LauncherCompilerFlags {
    return @('/nologo', '/target:exe', '/reference:System.Web.Extensions.dll')
}

function Ensure-Launcher([string]$Root, [string]$Bin, [string]$Work) {
    Assert-Launcher $Root $Bin
    $path = Join-Path $Bin 'abs-census-etl.exe'

    $recordPath = Join-Path $Root 'launcher.json'
    $record = Read-JsonFile $recordPath
    # Preserve the trusted spelling, including in case-sensitive directories.
    # Only comparisons are case-insensitive; no runtime Unicode case folding
    # enters the persisted identity or generated launcher's path.
    $literalRoot = $Root
    if ([string]::Equals((Get-Field $record 'root'), $Root,
            [StringComparison]::OrdinalIgnoreCase)) {
        $literalRoot = Get-Field $record 'root'
    }
    $launcherSource = Get-LauncherSource $literalRoot
    $compilerFlags = @(Get-LauncherCompilerFlags)
    $digest = Get-TextDigest ($launcherSource + "`n" + ($compilerFlags -join "`n"))
    $identity = (Get-TextDigest $literalRoot).Substring(0, 12)
    Remove-StaleLaunchers $Root $Bin $identity
    if ([IO.File]::Exists($path) -and
        [string]::Equals((Get-Field $record 'bin'), $Bin,
            [StringComparison]::OrdinalIgnoreCase) -and
        (Get-Field $record 'source_sha256') -ceq $digest -and
        (Get-Field $record 'binary_sha256') -cmatch '\A[0-9a-f]{64}\z' -and
        (Get-Acl -LiteralPath $path).GetOwner([Security.Principal.SecurityIdentifier]) -eq
            [Security.Principal.WindowsIdentity]::GetCurrent().User) {
        # Lock out other writers before trusting the bytes; foreign files are rebuilt.
        Set-PrivateFile $path
        if ((Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant() -ceq
            (Get-Field $record 'binary_sha256')) { return $path }
    }
    Write-Host 'Building and publishing managed launcher...'
    $source = Join-Path $Work 'launcher.cs'
    [IO.File]::WriteAllText($source, $launcherSource.Replace('LAUNCHER_DIGEST', $digest))
    $pendingName = '.abs-etl-' + $identity + '-launcher-' + [guid]::NewGuid().ToString('N')
    $pending = Join-Path $Bin ($pendingName + '.exe')
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    if (-not [IO.File]::Exists($compiler)) {
        throw 'The Windows .NET Framework compiler is required.'
    }
    try {
        [void](Invoke-CheckedNative $compiler ($compilerFlags + @(('/out:' + $pending), $source)) `
            $Work -Isolated)
        Set-PrivateFile $pending
        $binaryHash = (Get-FileHash -LiteralPath $pending).Hash.ToLowerInvariant()
        # Only source upgrades need the rename pair; unchanged runs leave the
        # executable identity untouched. A denied rename preserves the old app.
        $previousName = '.abs-etl-' + $identity + '-previous-' + [guid]::NewGuid().ToString('N')
        $previous = Join-Path $Bin ($previousName + '.exe')
        if ([IO.File]::Exists($path)) {
            Invoke-SharingRetry { [IO.File]::Move($path, $previous) }
        }
        try { Invoke-SharingRetry { [IO.File]::Move($pending, $path) } }
        catch {
            if ([IO.File]::Exists($previous)) { [IO.File]::Move($previous, $path) }
            throw
        }
        if ([IO.File]::Exists($previous)) {
            try { [IO.File]::Delete($previous) }
            catch {
                Write-Warning "Old launcher is still in use: $previous. Rerun later for cleanup."
            }
        }
        $trusted = @{ root = $literalRoot; bin = $Bin; source_sha256 = $digest
            binary_sha256 = $binaryHash }
        Write-AtomicText $recordPath ($trusted | ConvertTo-Json)
    } finally { if ([IO.File]::Exists($pending)) { [IO.File]::Delete($pending) } }
    return $path
}

function Install-AbsCensus {
    param([switch]$Csv, [switch]$Parquet, [string]$Version, [string]$Prefix,
          [string]$BinDir, [string]$Repository, [string]$ReleaseDir, [string]$Uv,
          [switch]$UseNetworkEnv, [switch]$Help)
    if ($Help) {
        Write-Host @'
Usage: .\install.ps1 [-Csv | -Parquet] [-Version VERSION]
                    [-Prefix ABSOLUTE_PATH] [-BinDir ABSOLUTE_PATH]
                    [-Repository OWNER/REPO] [-ReleaseDir ABSOLUTE_PATH -Version VERSION]
                    [-Uv ABSOLUTE_PATH] [-UseNetworkEnv]
Download a fresh installer on every install/update. Saved choices are retained.
Initial format: CSV. Native x64 Windows 10 (1803+)/11; no elevation or activation needed.
No persistent PATH or execution-policy changes are made. Full launcher path is printed.
'@
        return
    }
    if ($Csv -and $Parquet) { throw 'Choose only one of -Csv and -Parquet.' }
    if ($Version -and -not (Test-ReleaseVersion $Version)) { throw 'Invalid public version.' }
    if ($ReleaseDir -and -not $Version) { throw '-ReleaseDir requires -Version.' }
    if ($Repository) { Assert-Repository $Repository }
    Assert-StandardUser
    $target = Get-ManagedTarget
    [void](Get-SystemTool 'tar.exe')
    if (-not $Prefix) { $Prefix = Join-Path $env:LOCALAPPDATA 'abs-census-etl' }
    $Prefix = Get-InstallPath $Prefix
    Assert-OwnedDirectory $Prefix
    # CPython and all dependencies must fit without machine-wide long-path changes.
    # LongPathsEnabled alone is insufficient for every native dependency/tool.
    if ($Prefix.Length -gt 100) {
        throw 'Installation root exceeds 100 characters. Choose a shorter -Prefix (e.g. C:\ETL).'
    }
    # The root itself is made private on every run; only folders above it count.
    Assert-ProtectedLocation ([IO.Path]::GetDirectoryName($Prefix))
    if ($ReleaseDir) {
        $ReleaseDir = Get-InstallPath $ReleaseDir
        Assert-PlainPath $ReleaseDir
        if (-not [IO.Directory]::Exists($ReleaseDir)) { throw '-ReleaseDir is not a directory.' }
    }
    if ($Uv) {
        $Uv = Get-InstallPath $Uv
        Assert-PlainFile $Uv
        if (-not [IO.File]::Exists($Uv) -or [IO.Path]::GetExtension($Uv) -ine '.exe') {
            throw '-Uv must name a trusted .exe executable.'
        }
    }
    $marker = Join-Path $Prefix '.abs-census-etl'
    Assert-PlainFile $marker
    if ([IO.Directory]::Exists($Prefix)) {
        if (-not [IO.File]::Exists($marker)) {
            $otherFiles = @(Get-ChildItem -LiteralPath $Prefix -Force |
                Where-Object Name -ne '.install-lock')
            $markerPending = [IO.File]::Exists((Join-Path $Prefix '.install-lock')) -and
                @($otherFiles | Where-Object Name -NotMatch '\A\.part-[0-9a-f-]{36}\z').Count -eq 0
            if ($otherFiles.Count -and -not $markerPending) {
                throw "Not a managed installation: $Prefix"
            }
        } elseif ([IO.File]::ReadAllText($marker).Trim() -ne 'windows-1') {
            throw 'Unsupported installation layout.'
        }
    }
    $activePath = Join-Path $Prefix 'active.json'
    $settingsPath = Join-Path $Prefix 'settings.json'
    $active = Read-JsonFile $activePath
    $settings = Read-JsonFile $settingsPath
    if (-not (Test-Selection $active)) { $active = $null }
    if ($null -ne $active) { $settings = $active }
    elseif ([IO.File]::Exists($settingsPath) -and $null -eq $settings) {
        throw 'Invalid settings.json; inspect it or use a fresh installation root.'
    }
    $format = 'csv'
    if (Get-Field $settings 'format') { $format = Get-Field $settings 'format' }
    if ($Csv) { $format = 'csv' }; if ($Parquet) { $format = 'parquet' }
    if ($format -cnotin @('csv', 'parquet')) { throw 'Invalid saved format.' }
    $defaultRepo = 'Agent-Cognitive-Enterprise/abs-census-etl'
    if (-not $Repository) { $Repository = Get-Field $settings 'repository' }
    if (-not $Repository) { $Repository = $defaultRepo }
    Assert-Repository $Repository
    if ($Repository -cne $defaultRepo) {
        Write-Host "Using non-default release repository: $Repository"
        Write-Host "To reset, pass -Repository $defaultRepo explicitly."
    }
    if (-not $BinDir) { $BinDir = Get-Field $settings 'bin_dir' }
    if (-not $BinDir) { $BinDir = Join-Path $Prefix 'bin' }
    $BinDir = Get-InstallPath $BinDir
    Assert-OwnedDirectory $BinDir
    Assert-ProtectedLocation $BinDir -Contents -Stop $Prefix
    Assert-Launcher $Prefix $BinDir
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    if (-not [IO.File]::Exists($compiler)) { throw 'Windows .NET Framework is required.' }
    New-PrivateDirectory $Prefix
    $lockPath = Join-Path $Prefix '.install-lock'
    Assert-PlainFile $lockPath
    $lock = Open-InstallLock $lockPath
    $work = ''; $candidate = ''; $retain = $false
    try {
        if (-not [IO.File]::Exists($marker) -and
            @(Get-ChildItem -LiteralPath $Prefix -Force |
                Where-Object Name -ne '.install-lock').Count) {
            throw "Not a managed installation: $Prefix. Inspect incomplete first-install files."
        }
        Set-PrivateDirectory $Prefix
        if (-not [IO.File]::Exists($marker)) { Write-AtomicText $marker 'windows-1' }
        $lock.SetLength(0)
        $bytes = [Text.Encoding]::UTF8.GetBytes("$PID`n")
        $lock.Write($bytes, 0, $bytes.Length); $lock.Flush()
        foreach ($directory in @('versions', 'tools', 'python', 'cache')) {
            $path = Join-Path $Prefix $directory
            Assert-OwnedDirectory $path
            if (-not [IO.Directory]::Exists($path)) {
                [void][IO.Directory]::CreateDirectory($path)
                Set-PrivateDirectory $path
            }
        }
        # A bin folder created here is private; an existing one was checked above.
        New-PrivateDirectory $BinDir
        $work = Join-Path $Prefix ('.install-' + [guid]::NewGuid().ToString('N'))
        [void][IO.Directory]::CreateDirectory($work)
        [IO.File]::WriteAllText((Join-Path $work 'uv.toml'), '')
        foreach ($part in @('user', 'system')) {
            $config = Join-Path $work "config\$part\uv"
            [void][IO.Directory]::CreateDirectory($config)
            [IO.File]::WriteAllText((Join-Path $config 'uv.toml'), '')
        }
        Write-Host 'Checking selected release and cached environments...'
        if (-not $Version) {
            $resolved = Resolve-Release $Repository
            $Version = $resolved.Version; $assetRepo = $resolved.Repository
        } else { $assetRepo = $Repository }
        $context = [pscustomobject]@{ Root = $Prefix; Work = $work; Uv = $Uv
            Network = [bool]$UseNetworkEnv; ReleaseDir = $ReleaseDir
            AssetUrl = "https://github.com/$assetRepo/releases/download/v$Version" }
        foreach ($slot in Get-ChildItem -LiteralPath (Join-Path $Prefix 'versions') -Directory) {
            Assert-OwnedDirectory $slot.FullName
            $record = Read-JsonFile (Join-Path $slot.FullName 'release.json')
            if ((Get-Field $record 'version') -ceq $Version -and
                (Get-Field $record 'repository') -ceq $Repository -and
                (Get-Field $record 'format') -ceq $format -and
                (Get-Field $record 'target') -ceq $target -and
                (Test-Environment $slot.FullName $Version $format)) {
                $candidate = $slot.FullName; $retain = $true; break
            }
        }
        if (-not $candidate) {
            Write-Host "Downloading and verifying release $Version ($format)..."
            $checksums = Join-Path $work 'SHA256SUMS'
            if ($ReleaseDir) { [IO.File]::Copy((Join-Path $ReleaseDir 'SHA256SUMS'), $checksums) }
            else { [void](Get-HttpsFile "$($context.AssetUrl)/SHA256SUMS" $checksums) }
            $source = Get-VerifiedAsset $context "abs_census_etl-$Version.tar.gz"
            $wheel = Get-VerifiedAsset $context "abs_census_etl-$Version-py3-none-any.whl"
            Assert-SourceArchive $source $Version $work
            if (-not $context.Uv) {
                $context.Uv = Join-Path $Prefix ('tools\uv-' + $script:UvVersion + '.exe')
                Assert-PlainFile $context.Uv
                if (-not [IO.File]::Exists($context.Uv)) {
                    $zip = Join-Path $work 'uv.zip'
                    $base = 'https://github.com/astral-sh/uv/releases/download/' + $script:UvVersion
                    [void](Get-HttpsFile "$base/uv-x86_64-pc-windows-msvc.zip" $zip)
                    $bootstrapHash = (Get-FileHash -LiteralPath $zip).Hash.ToLowerInvariant()
                    if ($bootstrapHash -cne $script:UvSha256) {
                        throw 'uv bootstrap checksum mismatch.'
                    }
                    Expand-UvZip $zip $work
                    [IO.File]::Move((Join-Path $work 'uv.exe'), $context.Uv)
                }
            }
            $report = Invoke-Uv $context @('--version') $work
            $versionPattern = '\Auv ' + [regex]::Escape($script:UvVersion) + '( \(.*\))?\z'
            if ($report -cnotmatch $versionPattern) {
                throw "Installer requires uv $script:UvVersion."
            }
            Write-Host 'Preparing private Python 3.14 (download may take several minutes)...'
            [void](Invoke-Uv $context @('python', 'install', '--no-bin',
                '--no-registry', '3.14') $work)
            $python = Invoke-Uv $context @('python', 'find', '--managed-python',
                '--no-python-downloads', '3.14') $work
            $slotName = 'release-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
            $candidate = Join-Path (Join-Path $Prefix 'versions') $slotName
            $sourceDir = Join-Path $candidate 'source'
            [void][IO.Directory]::CreateDirectory($sourceDir)
            Set-PrivateDirectory $candidate
            [IO.File]::WriteAllText((Join-Path $candidate 'uv.toml'), '')
            $tar = (Get-SystemTool 'tar.exe')
            [void](Invoke-CheckedNative $tar @('-xzf', $source, '-C', $sourceDir,
                '--strip-components=1') $work -Isolated)
            $probe = @'
import pathlib, sys, sysconfig, tomllib
root, version = pathlib.Path(sys.argv[1]), sys.argv[2]
if sys.version_info[:2] != (3, 14) or sysconfig.get_config_var('Py_GIL_DISABLED'):
    raise SystemExit('Standard CPython 3.14 is required')
for name in ('abs_census_etl.py', 'pyproject.toml', 'uv.lock', 'setup.cfg'):
    if not (root / name).is_file(): raise SystemExit('Missing release input: '+name)
metadata = tomllib.loads((root / 'pyproject.toml').read_text())
if metadata['project']['name'] != 'abs-census-etl' or metadata['project']['version'] != version:
    raise SystemExit('Release tag and project metadata disagree')
if 'required-version' not in metadata.get('tool', {}).get('uv', {}):
    raise SystemExit('Release must define tool.uv.required-version')
lock = tomllib.loads((root / 'uv.lock').read_text())
for package in lock['package']:
    name = package['name']
    if not name or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-._' for c in name):
        raise SystemExit('Invalid dependency name in lock')
'@
            [void](Invoke-CheckedNative $python @('-I', '-X', 'utf8', '-', $sourceDir, $Version) `
                $work -Isolated -InputText $probe)
            $arguments = @('sync', '--locked', '--no-install-project', '--no-dev',
                '--managed-python', '--python', $python, '--no-build')
            if ($format -eq 'parquet') { $arguments += @('--extra', 'parquet') }
            Write-Host 'Installing locked dependencies (download may take several minutes)...'
            [void](Invoke-Uv $context $arguments $sourceDir)
            $venvPython = Join-Path $sourceDir '.venv\Scripts\python.exe'
            [void](Invoke-Uv $context @('pip', 'install', '--python', $venvPython,
                '--no-deps', '--no-build', $wheel) $sourceDir)
            Write-Host 'Verifying installed application...'
            if (-not (Test-Environment $candidate $Version $format)) {
                throw 'Installed version/import/CLI smoke failed.'
            }
            $record = @{ version = $Version; repository = $Repository; format = $format
                target = $target
                source_sha256 = (Get-FileHash -LiteralPath $source).Hash.ToLowerInvariant()
                wheel_sha256 = (Get-FileHash -LiteralPath $wheel).Hash.ToLowerInvariant() }
            Write-AtomicText (Join-Path $candidate 'release.json') ($record | ConvertTo-Json)
        } else { Write-Host "Reusing prepared release $Version ($format)." }
        Write-Host 'Checking managed launcher...'
        $launcher = Ensure-Launcher $Prefix $BinDir $work
        if ($null -ne $active -and
            (Get-Field $active 'slot') -ceq [IO.Path]::GetFileName($candidate) -and
            (Get-Field $active 'bin_dir') -ceq $BinDir) {
            try { Write-AtomicText $settingsPath ($active | ConvertTo-Json) }
            catch { Write-Warning 'App is active; fallback settings could not be saved.' }
            Write-Host "Already installed: $Version ($format). $launcher"
            return
        }
        $selection = @{ slot = [IO.Path]::GetFileName($candidate); version = $Version
            format = $format; repository = $Repository; target = $target; bin_dir = $BinDir }
        # Retain a ready candidate even if interrupted immediately after atomic publication.
        $retain = $true
        Write-AtomicText $activePath ($selection | ConvertTo-Json)
        try { Write-AtomicText $settingsPath ($selection | ConvertTo-Json) }
        catch {
            Write-Warning 'App activated; fallback settings could not be saved. Rerun to retry.'
        }
        Write-Host "Installed $Version ($format): $launcher"
        Write-Host ("Run: & '" + $launcher.Replace("'", "''") + "' --help")
        Write-Host 'No persistent PATH or execution-policy changes were made.'
    } finally {
        if ($candidate -and -not $retain -and [IO.Directory]::Exists($candidate)) {
            try { Remove-Item -LiteralPath $candidate -Recurse -Force }
            catch { Write-Warning "Retained failed candidate: $candidate" }
        }
        if ($work -and [IO.Directory]::Exists($work)) {
            try { Remove-Item -LiteralPath $work -Recurse -Force }
            catch { Write-Warning "Retained installer workspace: $work" }
        }
        $lock.Dispose()
        # DeleteOnClose releases/removes the lock even after abrupt process exit.
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Install-AbsCensus @PSBoundParameters }
    catch { [Console]::Error.WriteLine('ERROR: ' + $_.Exception.Message); exit 1 }
}
