#Requires -Version 5.1
# CI adapter: run a check with Administrator membership disabled, never an installer elevated.
param([Parameter(Mandatory=$true)][string]$Script,
      [Parameter(Mandatory=$true)][string]$Output,[string]$Engine='')
$ErrorActionPreference='Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1')
if (-not $Engine) {
    $Engine=Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
}
$Script=$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Script)
$Output=$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
if ($Script.Contains('"') -or $Engine.Contains('"')) { throw 'Invalid script/engine path' }
[void][IO.Directory]::CreateDirectory((Split-Path $Output))
$principal=New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $result=Invoke-Native $Engine @('-NoProfile','-ExecutionPolicy','Bypass','-File',$Script) `
        (Split-Path $Script)
    [IO.File]::WriteAllText($Output, $result.Stdout+$result.Stderr)
    Get-Content $Output
    exit $result.ExitCode
}
Add-Type @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
public static class StandardRunner {
 [StructLayout(LayoutKind.Sequential)] struct SA {
  public int size; public IntPtr sd; public bool inherit;
 }
 [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct SI {
  public int cb; public string reserved,desktop,title;
  public int x,y,xs,ys,xc,yc,fill,flags; public short show,reserved2;
  public IntPtr reservedP,input,output,error;
 }
 [StructLayout(LayoutKind.Sequential)] struct PI {
  public IntPtr process,thread; public uint pid,tid;
 }
 [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
 [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr h,uint ms);
 [DllImport("kernel32.dll")] static extern bool GetExitCodeProcess(IntPtr h,out uint code);
 [DllImport("kernel32.dll",SetLastError=true)]
 static extern bool SetHandleInformation(IntPtr h,uint m,uint f);
 [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr p);
 [DllImport("advapi32.dll",SetLastError=true)]
 static extern bool OpenProcessToken(IntPtr p,uint rights,out IntPtr t);
 [DllImport("advapi32.dll",SetLastError=true)]
 static extern bool SaferCreateLevel(uint scope,uint level,uint flags,out IntPtr h,IntPtr r);
 [DllImport("advapi32.dll",SetLastError=true)]
 static extern bool SaferComputeTokenFromLevel(IntPtr level,IntPtr input,out IntPtr output,
    uint flags,IntPtr r);
 [DllImport("advapi32.dll")] static extern bool SaferCloseLevel(IntPtr level);
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
 static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string s,uint revision,
    out IntPtr sd,out uint size);
 [DllImport("advapi32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
 static extern bool CreateProcessAsUser(IntPtr token,string app,string cmd,IntPtr pa,IntPtr ta,
    bool inherit,uint flags,IntPtr env,string cwd,ref SI si,out PI pi);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
 static extern IntPtr CreateWindowStation(string name,uint flags,uint access,ref SA sa);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
 static extern IntPtr CreateDesktop(string name,IntPtr device,IntPtr mode,uint flags,
    uint access,ref SA sa);
 [DllImport("user32.dll")] static extern IntPtr GetProcessWindowStation();
 [DllImport("user32.dll",SetLastError=true)] static extern bool SetProcessWindowStation(IntPtr h);
 [DllImport("user32.dll")] static extern bool CloseWindowStation(IntPtr h);
 [DllImport("user32.dll")] static extern bool CloseDesktop(IntPtr h);
 [DllImport("kernel32.dll",SetLastError=true)]
 static extern bool AssignProcessToJobObject(IntPtr job,IntPtr process);
 [DllImport("kernel32.dll",SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
 [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr process,uint code);
 public static uint Run(string command,string cwd,string output,IntPtr job) {
  IntPtr token=IntPtr.Zero,limited=IntPtr.Zero,level=IntPtr.Zero;
  IntPtr station=IntPtr.Zero,desktop=IntPtr.Zero,sd=IntPtr.Zero;
  try {
   if(!OpenProcessToken(GetCurrentProcess(),0xf01ff,out token)) throw new Win32Exception();
   if(!SaferCreateLevel(1,0x20000,1,out level,IntPtr.Zero)) throw new Win32Exception();
   if(!SaferComputeTokenFromLevel(level,token,out limited,0,IntPtr.Zero))
    throw new Win32Exception();
   string sid=WindowsIdentity.GetCurrent().User.Value; uint size;
   if(!ConvertStringSecurityDescriptorToSecurityDescriptor(
    "D:(A;;GA;;;"+sid+")(A;;GA;;;SY)S:(ML;;NW;;;LW)",1,out sd,out size)) throw new Win32Exception();
   var sa=new SA();sa.size=Marshal.SizeOf(sa);sa.sd=sd;
   string name="abs"+Guid.NewGuid().ToString("N");
   station=CreateWindowStation(name,0,0xf037f,ref sa);
   if(station==IntPtr.Zero) throw new Win32Exception();
   IntPtr old=GetProcessWindowStation();
   if(!SetProcessWindowStation(station)) throw new Win32Exception();
   try { desktop=CreateDesktop("test",IntPtr.Zero,IntPtr.Zero,0,0xf01ff,ref sa); }
   finally { SetProcessWindowStation(old); }
   if(desktop==IntPtr.Zero) throw new Win32Exception();
   using(var file=new FileStream(output,FileMode.Create,FileAccess.Write,FileShare.Read)) {
    if(!SetHandleInformation(file.SafeFileHandle.DangerousGetHandle(),1,1))
     throw new Win32Exception();
    var si=new SI();si.cb=Marshal.SizeOf(si);si.desktop=name+"\\test";
    si.flags=0x100;si.output=file.SafeFileHandle.DangerousGetHandle();si.error=si.output;
    PI pi;
    if(!CreateProcessAsUser(limited,null,command,IntPtr.Zero,IntPtr.Zero,true,0x08000004,
       IntPtr.Zero,cwd,ref si,out pi)) throw new Win32Exception();
    try {
     if(!AssignProcessToJobObject(job,pi.process)) {
      int error=Marshal.GetLastWin32Error(); TerminateProcess(pi.process,1);
      throw new Win32Exception(error);
     }
     if(ResumeThread(pi.thread)==0xffffffff) throw new Win32Exception();
     if(WaitForSingleObject(pi.process,3000000)!=0) throw new Exception("Test process timed out");
     uint code;if(!GetExitCodeProcess(pi.process,out code))throw new Win32Exception();return code;
    } finally {CloseHandle(pi.thread);CloseHandle(pi.process);}
   }
  } finally {
   if(desktop!=IntPtr.Zero)CloseDesktop(desktop);
   if(station!=IntPtr.Zero)CloseWindowStation(station);
   if(sd!=IntPtr.Zero)LocalFree(sd);
   if(limited!=IntPtr.Zero)CloseHandle(limited);
   if(level!=IntPtr.Zero)SaferCloseLevel(level);
   if(token!=IntPtr.Zero)CloseHandle(token);
  }
 }
}
'@
$command='"'+$Engine+'" -NoProfile -ExecutionPolicy Bypass -OutputFormat Text -File "'+$Script+'"'
$job=New-ChildJob
try { $code=[StandardRunner]::Run($command,(Split-Path $Script),$Output,$job) }
finally { [void][AbsInstallerJob]::CloseHandle($job) }
Get-Content $Output
exit $code
