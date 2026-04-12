"""
Route module import smoke test (Breadth-3).

The cheapest possible coverage — make sure every module in core/routes/
loads without errors. This catches the "you broke an import in routes/X.py"
class of bug that currently slips through, because nothing in the test
suite imports those modules at test time.

Why this matters: route modules don't get touched by `pytest tests/` unless
something in the test suite happens to import them. After Phase 4 we
rewrote ~47 imports across core/routes/{chat,knowledge}.py. If any of those
fully-qualified-path imports rotted (typo, file moved, bad refactor), the
suite passed anyway because nobody loaded the module.

IMPORT-ORDER GOTCHA
───────────────────
Several route modules (e.g. core.routes.agents) do
    `from core.api_fastapi import get_system`
at the top level. core.api_fastapi.py in turn imports the route modules. So
trying to `import core.routes.agents` standalone hits a circular import —
the route module hasn't finished defining `router` yet when api_fastapi
tries to read it.

Solution: bootstrap by importing `core.api_fastapi` first (the way the real
app does), THEN verify each route module ended up loaded in sys.modules.
This gives us the same coverage as importing each module individually plus
catches load-order bugs as a bonus.

Run with: pytest tests/test_route_imports.py -v
"""
import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def discover_route_modules():
    """Yield dotted module names for every .py file in core/routes/."""
    routes_dir = PROJECT_ROOT / "core" / "routes"
    if not routes_dir.exists():
        return []
    out = []
    for p in sorted(routes_dir.glob("*.py")):
        if p.name.startswith("_"):
            continue  # skip __init__.py
        out.append(f"core.routes.{p.stem}")
    return out


ROUTE_MODULES = discover_route_modules()


def test_api_fastapi_bootstraps_cleanly():
    """The full FastAPI app module must import without errors.

    This is the entrypoint in production. If it can't import, nothing works.
    """
    importlib.import_module("core.api_fastapi")


@pytest.mark.parametrize("module_name", ROUTE_MODULES)
def test_route_module_loaded_after_bootstrap(module_name):
    """Every route module must end up in sys.modules after api_fastapi imports.

    We bootstrap once (cheap — already cached) then check sys.modules. If a
    route module errored during import, it won't be in sys.modules and this
    test fails with a useful message.
    """
    importlib.import_module("core.api_fastapi")  # idempotent — already cached
    assert module_name in sys.modules, \
        f"{module_name} did not load — check for import errors in the module"

    mod = sys.modules[module_name]
    # Sanity: the module should have at least one defined attribute
    # (every routes/*.py defines `router = APIRouter()`).
    assert hasattr(mod, "router"), \
        f"{module_name} loaded but has no `router` — broken APIRouter setup?"
