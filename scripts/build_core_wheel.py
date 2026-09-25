"""Build a Core-only wheel from a clean staging directory."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="drone-core-") as temporary:
        stage = Path(temporary)
        for name in ("pyproject.toml", "README.md", "core_build_backend.py", "MANIFEST.in"):
            shutil.copy2(ROOT / name, stage / name)
        for name in ("contracts", "core"):
            shutil.copytree(ROOT / name, stage / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
        command = [sys.executable, "-m", "pip", "wheel", "--no-deps",
                   "--no-build-isolation", "--wheel-dir", str(output), str(stage)]
        return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
