"""Load explicitly selected Python botbases or plugins from .py files."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def load_plugin(path: str | Path) -> ModuleType:
    """Import trusted Python code in this process; plugins are not sandboxed."""
    path = Path(path).resolve()
    if path.suffix.lower() != ".py" or not path.is_file():
        raise ValueError(f"Not a Python plugin file: {path}")
    spec = importlib.util.spec_from_file_location(f"wotlkbuddy_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to import plugin: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
