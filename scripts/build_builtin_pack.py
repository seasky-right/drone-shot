"""Build the separately installable first-party pack from repository sources."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("builtin_pack", "agents", "backends", "evaluators", "simulator_contract", "tasks")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="drone-builtin-pack-") as temporary:
        stage = Path(temporary)
        shutil.copy2(ROOT / "packs" / "builtin" / "pyproject.toml", stage / "pyproject.toml")
        for name in PACKAGES:
            shutil.copytree(ROOT / name, stage / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
        command = [sys.executable, "-m", "pip", "wheel", "--no-deps",
                   "--no-build-isolation", "--wheel-dir", str(output), str(stage)]
        return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
