"""Loads a demo script by its file path, since lib/scripts/ is not a package."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

DEMO_SCRIPTS_DIR = Path(__file__).resolve().parents[4] / "scripts" / "demo"


def load_demo_script(name: str) -> ModuleType:
    """Import lib/scripts/demo/<name>.py as a module named demo_<name>."""
    module_name = f"demo_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, DEMO_SCRIPTS_DIR / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load demo script {name!r} from {DEMO_SCRIPTS_DIR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
