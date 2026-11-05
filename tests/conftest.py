"""Load the plugin folder as a package named `crystallize` (no Hermes needed)."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if "crystallize" not in sys.modules:
    spec = importlib.util.spec_from_file_location("crystallize", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["crystallize"] = pkg
    spec.loader.exec_module(pkg)
