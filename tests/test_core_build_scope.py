"""Regression for direct root builds after setuptools has cached other packages."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import unittest
import zipfile

import core_build_backend
from scripts.verify_wheel import failures


ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = {"MANIFEST.in", "PKG-INFO", "README.md", "core_build_backend.py",
                "pyproject.toml", "setup.cfg"}
SOURCE_DIRS = {"contracts", "core", "drone_platform.egg-info"}


class CoreBuildScopeTests(unittest.TestCase):
    def test_direct_wheel_and_sdist_contain_only_core_sources(self) -> None:
        with TemporaryDirectory(prefix="drone-core-build-test-") as temporary:
            output = Path(temporary)
            subprocess.run(
                [sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
                 "--no-build-isolation", "--wheel-dir", str(output)],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            wheels = list(output.glob("drone_platform-*.whl"))
            self.assertEqual(len(wheels), 1)
            with zipfile.ZipFile(wheels[0]) as archive:
                self.assertEqual(failures(archive.namelist()), [])

            name = core_build_backend.build_sdist(str(output))
            with tarfile.open(output / name, "r:gz") as archive:
                members = [Path(item.name).parts[1:] for item in archive.getmembers()
                           if len(Path(item.name).parts) > 1]
            self.assertTrue(members)
            unexpected = {parts[0] for parts in members
                          if parts[0] not in SOURCE_FILES | SOURCE_DIRS}
            self.assertEqual(unexpected, set())
            self.assertIn(("core_build_backend.py",), members)
            self.assertIn(("core", "conformance.py"), members)


if __name__ == "__main__":
    unittest.main()
