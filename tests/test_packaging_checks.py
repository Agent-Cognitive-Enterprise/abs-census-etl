"""Offline regressions for packaging-check enforcement."""
from pathlib import Path
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest

CHECKS = runpy.run_path(str(Path(__file__).with_name("check_packaging.py")))
check_files = CHECKS["check_files"]
copy_build_source = CHECKS["copy_build_source"]
guarded_workspace = CHECKS["guarded_workspace"]
isolated_environment = CHECKS["isolated_environment"]
uv_environment = CHECKS["uv_environment"]
write_decoys = CHECKS["write_decoys"]
DECOY_FILES = CHECKS["DECOY_FILES"]
PLANTED_PARENT_CONFIG = CHECKS["PLANTED_PARENT_CONFIG"]
SOURCE_FILES = CHECKS["SOURCE_FILES"]
UV = shutil.which("uv")


class PackagingCheckTests(unittest.TestCase):
    def test_actual_source_has_all_required_build_inputs(self):
        # Missing newly added docs must fail offline, including in a clean clone.
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            copy_build_source(root, Path(td) / "source")

    def test_missing_document_is_refused_offline(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source"
            copy_build_source(Path(__file__).resolve().parents[1], source)
            document = next(name for name in sorted(SOURCE_FILES) if name.startswith("docs/"))
            (source / document).unlink()
            with self.assertRaisesRegex(RuntimeError, "Missing build input"):
                copy_build_source(source, Path(td) / "destination")

    def make_source(self, root):
        for name in SOURCE_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)

    def test_build_copy_excludes_unrelated_checkout_data(self):
        with tempfile.TemporaryDirectory() as td:
            source, destination = Path(td) / "source", Path(td) / "build"
            self.make_source(source)
            for name in ("data.zip", "abs_out/data.csv", "abs_cache/data.zip",
                         "other-venv/Lib/package.py"):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("unrelated")
            if os.name == "posix":
                (source / "unrelated-link").symlink_to(source / "other-venv",
                                                       target_is_directory=True)
            copy_build_source(source, destination)
            self.assertEqual({p.relative_to(destination).as_posix()
                              for p in destination.rglob("*") if p.is_file()}, SOURCE_FILES)
            for name in SOURCE_FILES:
                self.assertEqual((destination / name).read_text(), name)

    @unittest.skipUnless(os.name == "posix", "POSIX symlink creation")
    def test_build_copy_refuses_selected_file_or_directory_symlinks(self):
        for name in ("abs_census_etl.py", "tests"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                source, destination = Path(td) / "source", Path(td) / "build"
                self.make_source(source)
                path = source / name
                original = path.with_name(path.name + ".original")
                path.rename(original)
                path.symlink_to(original, target_is_directory=original.is_dir())
                with self.assertRaisesRegex(RuntimeError, "Symlinked build input"):
                    copy_build_source(source, destination)

    def test_decoy_placeholders_are_disjoint_and_preserve_build_inputs(self):
        self.assertFalse(SOURCE_FILES & set(DECOY_FILES))
        with tempfile.TemporaryDirectory() as td:
            source, destination = Path(td) / "source", Path(td) / "build"
            self.make_source(source)
            copy_build_source(source, destination)
            write_decoys(destination)
            for name in SOURCE_FILES:
                self.assertEqual((destination / name).read_text(), name)
            for name in DECOY_FILES:
                self.assertTrue((destination / name).is_file(), name)

    def test_source_allowlist_rejects_scratch_files(self):
        with self.assertRaisesRegex(RuntimeError, "extra=.*tests/scratch.py"):
            check_files({"module.py", "tests/scratch.py"}, {"module.py"}, "sdist")

    def test_packaging_environment_removes_external_resolver_settings(self):
        original = {"UV_INDEX_URL": "bad", "UV_OVERRIDE": "bad", "UV_EXCLUDE_NEWER": "bad",
                    "uv_config_file": "bad", "PIP_INDEX_URL": "bad", "VIRTUAL_ENV": "bad",
                    "PYTHONPATH": "bad", "PYTHONHOME": "bad", "PATH": "keep",
                    "PYTHONOPTIMIZE": "1", "APPDATA": "user"}
        self.assertEqual(isolated_environment(original),
                         {"PATH": "keep", "PYTHONOPTIMIZE": "1", "APPDATA": "user"})

    def test_uv_config_redirection_is_limited_to_uv_commands(self):
        env = {"PATH": "keep", "APPDATA": "user", "XDG_CONFIG_HOME": "user"}
        with tempfile.TemporaryDirectory() as td:
            uv_env = uv_environment(env, Path(td))
            redirected = {"XDG_CONFIG_HOME", "XDG_CONFIG_DIRS", "APPDATA", "PROGRAMDATA"}
            self.assertEqual({k: v for k, v in uv_env.items() if k not in redirected},
                             {"PATH": "keep"})
            for key in redirected:
                self.assertTrue((Path(uv_env[key]) / "uv/uv.toml").is_file())
                self.assertEqual((Path(uv_env[key]) / "uv/uv.toml").read_text(), "")
        # Launchers, Python probes and pip keep the caller's own directories.
        self.assertEqual(env, {"PATH": "keep", "APPDATA": "user", "XDG_CONFIG_HOME": "user"})

    def test_guarded_workspace_shadows_planted_parent_config(self):
        with tempfile.TemporaryDirectory() as td:
            work = guarded_workspace(Path(td))
            self.assertEqual(work.parent, Path(td))
            self.assertEqual((work / "uv.toml").read_text(), "")
            self.assertEqual((Path(td) / "uv.toml").read_text(), PLANTED_PARENT_CONFIG)
        self.assertIn('required-version = "==0.0.1"', PLANTED_PARENT_CONFIG)
        self.assertIn(".invalid/", PLANTED_PARENT_CONFIG)

    @unittest.skipUnless(UV, "uv not on PATH")
    def test_uv_reads_planted_parent_config_only_without_guard(self):
        # Offline: uv venv with an explicit interpreter, no index or downloads.
        with tempfile.TemporaryDirectory() as td:
            work = guarded_workspace(Path(td))
            unguarded = Path(td) / "unguarded"
            unguarded.mkdir()
            env = dict(uv_environment(isolated_environment(dict(os.environ)), work / "config"),
                       UV_CACHE_DIR=str(Path(td) / "cache"), UV_OFFLINE="1")
            for cwd, guarded in ((work, True), (unguarded, False)):
                with self.subTest(guarded=guarded):
                    result = subprocess.run([UV, "venv", "--python", sys.executable,
                                             str(cwd / "env")], cwd=cwd, env=env,
                                            capture_output=True, text=True, timeout=60)
                    if guarded:
                        self.assertEqual(result.returncode, 0, result.stderr)
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("==0.0.1", result.stderr)


if __name__ == "__main__":
    unittest.main()
