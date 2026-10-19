"""Required PowerShell syntax check; exits 2 when no parser is installed.

Use an installed pwsh or ABS_ETL_PWSH.
No tools are downloaded and no Windows commands are executed by this check.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def find_powershell() -> str | None:
    if os.name == "nt":
        return str(Path(os.environ["WINDIR"]) /
                   "System32/WindowsPowerShell/v1.0/powershell.exe")
    return os.environ.get("ABS_ETL_PWSH") or shutil.which("pwsh")


def main() -> int:
    shell = find_powershell()
    if not shell:
        print("PowerShell parser is required: install pwsh or set ABS_ETL_PWSH "
              "to its executable. See README validation instructions.", file=sys.stderr)
        return 2
    script = r"""
$ErrorActionPreference = 'Stop'
$files = @('install.ps1', 'tests/test_windows_installer.ps1',
           'tests/check_windows_installer.ps1', 'tests/check_windows_elevation.ps1')
foreach ($path in $files) {
    $tokens = $null; $errors = $null
    [void][Management.Automation.Language.Parser]::ParseFile(
        (Join-Path (Get-Location) $path), [ref]$tokens, [ref]$errors)
    if ($errors) { throw ($errors | Out-String) }
    Write-Output ('PARSE: ' + $path)
}
"""
    return subprocess.run([shell, "-NoProfile", "-Command", script], cwd=ROOT,
                          timeout=60).returncode


if __name__ == "__main__":
    raise SystemExit(main())
