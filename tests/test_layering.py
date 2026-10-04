"""Invariant 6: `indicators/` and `detect/` are pure.

They may import no DB, no network, no clock, no web framework, and none of the app's
own I/O layers. Checked by reading the source with `ast` rather than importing it, so a
forbidden import fails here even if nothing calls it yet.
"""

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "live_macd_searcher"
PURE_LAYERS = ("indicators", "detect")

# `time` and `datetime` are the clock: bar logic keys off the exchange's open_time
# (invariant 2). If a pure module ever needs datetime for formatting alone, narrow this
# to a check for `.now()` then — not before.
FORBIDDEN_MODULES = {
    "asyncio", "datetime", "fastapi", "httpx", "socket", "sqlite3", "starlette",
    "time", "urllib", "uvicorn", "websockets",
}
IO_LAYERS = {"ingest", "store", "web"}


def _imported_names(source: str) -> set[str]:
    """Top-level stdlib/third-party modules, plus app layers, that a module imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
                if alias.name.startswith("live_macd_searcher."):
                    names.add(alias.name.split(".")[1])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                parts = node.module.split(".")
                names.add(parts[0])
                if parts[0] == "live_macd_searcher" and len(parts) > 1:
                    names.add(parts[1])
            elif node.module:
                # Relative: `from ..store import x` names the store layer.
                names.add(node.module.split(".")[0])
            else:
                # Relative with no module: `from .. import store`.
                names.update(alias.name for alias in node.names)
    return names


def _pure_sources() -> list[Path]:
    return sorted(p for layer in PURE_LAYERS for p in (PACKAGE_ROOT / layer).rglob("*.py"))


def test_pure_layers_exist():
    # Guards against the check below passing vacuously because the path is wrong.
    for layer in PURE_LAYERS:
        assert (PACKAGE_ROOT / layer / "__init__.py").is_file()


def _relative_id(path: Path) -> str:
    return path.relative_to(PACKAGE_ROOT).as_posix()


@pytest.mark.parametrize("path", _pure_sources(), ids=_relative_id)
def test_pure_layer_imports_no_io(path: Path):
    imported = _imported_names(path.read_text(encoding="utf-8"))
    assert not imported & FORBIDDEN_MODULES, f"clock or I/O import: {imported & FORBIDDEN_MODULES}"
    assert not imported & IO_LAYERS, f"imports an I/O layer: {imported & IO_LAYERS}"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import sqlite3", {"sqlite3"}),
        ("from datetime import datetime", {"datetime"}),
        ("from live_macd_searcher.store import db", {"live_macd_searcher", "store"}),
        ("from ..ingest import feed", {"ingest"}),
        ("from .. import web", {"web"}),
        ("import math", {"math"}),
    ],
)
def test_imported_names_sees_every_import_form(source: str, expected: set[str]):
    # The check is only as good as its parser; pin each import shape it must catch.
    assert _imported_names(source) == expected
