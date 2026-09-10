"""
Jenny - A lightweight AI agent framework
"""

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path


def _read_pyproject_version() -> str | None:
    """Read the source-tree version when package metadata is unavailable."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data.get("project", {}).get("version")


def _resolve_version() -> str:
    try:
        return _pkg_version("jenny")
    except PackageNotFoundError:
        # Su Android nessuna delle due fonti esiste — l'APK non porta né
        # pyproject.toml né i metadata del pacchetto — quindi questo letterale
        # è la versione che l'app mostra davvero. Va tenuto allineato a
        # pyproject.toml: ci pensa tests/test_package_version.py.
        return _read_pyproject_version() or "0.11.0"


__version__ = _resolve_version()
__logo__ = "✿"

__all__ = ["__version__", "__logo__"]
