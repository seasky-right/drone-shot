"""Create or verify the SHA-256 manifest for the frozen UE4/AirSim baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Iterable


WORKSPACE = Path(__file__).resolve().parent.parent
MANIFEST = Path(__file__).resolve().with_name("legacy_ue4_airsim.manifest.json")
SOURCES = (
    WORKSPACE / "材料-无人机遥感实习-AirSIM部分",
    WORKSPACE / "airsim-settings" / "drone.json",
)
EXCLUDED_PATHS = (
    "**/Saved/Logs/**",
    "**/Saved/Crashes/**",
    "**/__pycache__/**",
    "**/PythonClient/build/**",
    "**/build/**",
    "**/dist/**",
)


def is_excluded(relative_to_source: Path) -> bool:
    parts = tuple(part.lower() for part in PurePosixPath(relative_to_source.as_posix()).parts)
    if "__pycache__" in parts or "build" in parts or "dist" in parts:
        return True
    if any(parts[index : index + 2] in (("saved", "logs"), ("saved", "crashes")) for index in range(len(parts) - 1)):
        return True
    return any(parts[index : index + 2] == ("pythonclient", "build") for index in range(len(parts) - 1))


def source_files(source: Path) -> Iterable[Path]:
    if source.is_file():
        yield source
        return
    for path in sorted(source.rglob("*")):
        if path.is_file() and not is_excluded(path.relative_to(source)):
            yield path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest() -> dict[str, object]:
    files = []
    for source in SOURCES:
        if not source.exists():
            raise FileNotFoundError(f"Frozen baseline source is missing: {source}")
        for path in source_files(source):
            files.append(
                {
                    "path": path.relative_to(WORKSPACE).as_posix(),
                    "sha256": sha256(path),
                    "size_bytes": path.stat().st_size,
                }
            )
    return {
        "schema": "drone.legacy-baseline-manifest/v1",
        "algorithm": "sha256",
        "workspace_relative": True,
        "sources": [source.relative_to(WORKSPACE).as_posix() for source in SOURCES],
        "excluded": list(EXCLUDED_PATHS),
        "files": sorted(files, key=lambda item: str(item["path"])),
    }


def read_manifest() -> dict[str, object]:
    with MANIFEST.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("Manifest root must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze or verify the legacy UE4/AirSim baseline")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write the current baseline manifest")
    mode.add_argument("--verify", action="store_true", help="compare sources with the checked-in manifest")
    args = parser.parse_args()

    current = build_manifest()
    if args.write:
        with MANIFEST.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(current, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(f"WROTE files={len(current['files'])} manifest={MANIFEST}")
        return 0

    expected = read_manifest()
    if current == expected:
        print(f"VERIFIED files={len(current['files'])} manifest={MANIFEST}")
        return 0
    print("BASELINE_MISMATCH: regenerate only after intentional review of frozen inputs.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
