"""Run the Windows installer checks with the operating system's PowerShell 5.1."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PowerShellSyntaxTests(unittest.TestCase):
    def test_installer_and_checks_parse(self):
        import runpy
        helpers = runpy.run_path(str(ROOT / "tests/check_powershell_syntax.py"))
        shell = helpers["find_powershell"]()
        if not shell:
            self.skipTest("PowerShell is unavailable; native execution is checked separately")
        result = subprocess.run(
            [sys.executable, str(ROOT / "tests/check_powershell_syntax.py")],
            cwd=ROOT, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(os.name == "nt", "Native Windows PowerShell tests require Windows")
class WindowsInstallerTests(unittest.TestCase):
    def test_native_offline_installer_checks(self):
        powershell = Path(os.environ["WINDIR"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(ROOT / "tests/test_windows_installer.ps1")],
            cwd=ROOT, capture_output=True, text=True, timeout=180,
            env=dict(os.environ, ABS_INSTALLER_TEST_PYTHON=sys.executable),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("offline Windows installer assertions", result.stdout)


if __name__ == "__main__":
    unittest.main()
