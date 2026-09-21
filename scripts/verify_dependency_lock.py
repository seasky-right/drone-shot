"""Verify that the auditable build lock matches pyproject.toml exactly."""

from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
BUILD_LOCK = ROOT / "requirements-build.lock"


def requirements_from_lock(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def main() -> int:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared = project["build-system"]["requires"]
    locked = requirements_from_lock(BUILD_LOCK)
    if declared != locked:
        print("BUILD_LOCK_MISMATCH declared=" + repr(declared) + " locked=" + repr(locked))
        return 1
    print("BUILD_LOCK_VERIFIED requirements=" + ",".join(locked))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
