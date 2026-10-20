# conftest.py (repo root) - fixtures that must reach BOTH test paths in
# pytest.ini (tests/ and core/plugins/*/tests). tests/conftest.py only
# covers tests/; a plugin's own tests were writing real rows into the dev
# box's user/notifications.json the first time the full suite ran after the
# notification center landed (2026-10-20).
import tempfile
from pathlib import Path

import pytest

# The store path for the WHOLE test process, pinned at IMPORT time: a test
# module that imports core.api_fastapi at collection runs notify.init() before
# any fixture exists, and that read the real store and wrote the dev box's
# boot-result rows into it (2026-10-20). A scheduler or daemon thread that
# finishes after its test's teardown finds this path too, never the real one.
# The per-test fixture below layers a fresh file over it. The boot-result
# reader is parked for the session (the dev box has real result files); the
# unit test calls the live one by its other name.
try:
    from core import notify as _notify_mod
    _notify_mod.STORE = Path(tempfile.mkdtemp(prefix='sapphire-test-notify-')) / 'notifications.json'
    _notify_mod._boot_results_live = _notify_mod._boot_results
    _notify_mod._boot_results = lambda: None
except Exception:
    pass


@pytest.fixture(autouse=True)
def _hermetic_notify(tmp_path, monkeypatch):
    """The notification center (core/notify.py) is module state with a store
    file under user/. Every producer a test exercises (extras, env builds,
    device health, re-embed, the flasher, a scheduler publishing task events
    into the bus hooks) would otherwise write real rows. Each test gets an
    empty store in tmp and starts with no rows."""
    try:
        from core import notify as _n
    except Exception:
        return
    monkeypatch.setattr(_n, 'STORE', tmp_path / 'notifications.json')
    monkeypatch.setattr(_n, '_rows', {})
    monkeypatch.setattr(_n, '_last_tick', {})
    monkeypatch.setattr(_n, '_loaded', False)
