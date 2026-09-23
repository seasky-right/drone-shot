"""Check that a drone-platform wheel contains only distributable Python code.

Run after building a wheel, for example:
``python scripts/verify_wheel.py dist/drone_platform-0.1.0-py3-none-any.whl``.
The check deliberately uses only the standard library so it is usable in a
fresh virtual environment.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import PurePosixPath


PACKAGES = frozenset(
    {
        "agents",
        "backends",
        "contracts",
        "core",
        "evaluators",
        "simulator_contract",
        "tasks",
    }
)
PACKAGE_MARKERS = frozenset(
    {
        "agents/__init__.py",
        "backends/__init__.py",
        "backends/airsim/__init__.py",
        "backends/mock/__init__.py",
        "contracts/__init__.py",
        "core/__init__.py",
        "evaluators/__init__.py",
        "simulator_contract/__init__.py",
        "tasks/__init__.py",
    }
)
FORBIDDEN_PARTS = frozenset(
    {
        ".driver-cache",
        ".doc-review",
        "airsim-settings",
        "materials-无人机遥感实习-airsim部分",
        "材料-无人机遥感实习-airsim部分",
        "runs",
        "experiments",
        "tests",
        "fixtures",
    }
)
FORBIDDEN_SUFFIXES = frozenset({".dll", ".dmp", ".exe", ".pak", ".zip"})


def failures(names: list[str]) -> list[str]:
    """Return human-readable violations for the wheel member names."""
    violations: list[str] = []
    seen_packages: set[str] = set()
    for name in names:
        path = PurePosixPath(name)
        parts = path.parts
        if not parts or name.endswith("/"):
            continue
        top_level = parts[0]
        if top_level.endswith(".dist-info"):
            continue
        if top_level not in PACKAGES:
            violations.append(f"unexpected top-level wheel member: {name}")
            continue
        seen_packages.add(top_level)
        lower_parts = {part.casefold() for part in parts}
        if lower_parts & FORBIDDEN_PARTS or path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            violations.append(f"forbidden resource in wheel: {name}")
    missing = sorted(PACKAGES - seen_packages)
    if missing:
        violations.append(f"missing declared packages: {', '.join(missing)}")
    missing_markers = sorted(PACKAGE_MARKERS - set(names))
    if missing_markers:
        violations.append("missing package initializers: " + ", ".join(missing_markers))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", help="path to a built .whl file")
    args = parser.parse_args()
    try:
        with zipfile.ZipFile(args.wheel) as archive:
            issues = failures(archive.namelist())
    except (OSError, zipfile.BadZipFile) as error:
        print(f"WHEEL_INVALID: {error}", file=sys.stderr)
        return 2
    if issues:
        print("WHEEL_SCOPE_FAILED", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    print("WHEEL_SCOPE_VERIFIED packages=" + ",".join(sorted(PACKAGES)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
