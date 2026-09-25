"""Build the Core distribution from a clean, limited source stage.

Setuptools' wheel command can retain unrelated files already in build/lib.
Staging the declared Core sources avoids deleting user build output and makes
direct ``pip wheel .`` equivalent to the repository's clean build script.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Iterator

from setuptools import build_meta


ROOT = Path(__file__).resolve().parent


@contextmanager
def _stage() -> Iterator[None]:
    with TemporaryDirectory(prefix="drone-core-build-") as temporary:
        stage = Path(temporary)
        for name in ("pyproject.toml", "README.md", "MANIFEST.in", "core_build_backend.py"):
            shutil.copy2(ROOT / name, stage / name)
        for name in ("contracts", "core"):
            shutil.copytree(ROOT / name, stage / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
        previous = Path.cwd()
        try:
            os.chdir(stage)
            yield
        finally:
            os.chdir(previous)


def get_requires_for_build_wheel(config_settings=None):
    with _stage():
        return build_meta.get_requires_for_build_wheel(config_settings)


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    destination = str(Path(metadata_directory).resolve())
    with _stage():
        return build_meta.prepare_metadata_for_build_wheel(destination, config_settings)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    destination = str(Path(wheel_directory).resolve())
    metadata = None if metadata_directory is None else str(Path(metadata_directory).resolve())
    with _stage():
        return build_meta.build_wheel(destination, config_settings, metadata)


def get_requires_for_build_sdist(config_settings=None):
    with _stage():
        return build_meta.get_requires_for_build_sdist(config_settings)


def build_sdist(sdist_directory, config_settings=None):
    destination = str(Path(sdist_directory).resolve())
    with _stage():
        return build_meta.build_sdist(destination, config_settings)


# Editable installs must point at the live checkout, not the short-lived stage.
get_requires_for_build_editable = build_meta.get_requires_for_build_editable
prepare_metadata_for_build_editable = build_meta.prepare_metadata_for_build_editable
build_editable = build_meta.build_editable
