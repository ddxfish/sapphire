# conftest.py (repo root) - fixtures that must reach BOTH test paths in
# pytest.ini (tests/ and core/plugins/*/tests). tests/conftest.py only
# covers tests/; a plugin's own tests were writing real rows into the dev
# box's user/notifications.json the first time the full suite ran after the
# notification center landed (2026-10-20).
import pytest


@pytest.fixture(autouse=True, scope='session')
def _notify_store_for_the_session(tmp_path_factory):
    """The store path for the WHOLE test process. A scheduler or daemon
    thread that finishes after its test's teardown would otherwise find the
    real path restored and write there (four task-complete rows did, on the
    second full run). The per-test fixture below layers a fresh file over
    this; teardown restores THIS path, never the real one."""
    try:
        from core import notify as _n
    except Exception:
        yield
        return
    _n.STORE = tmp_path_factory.mktemp('notify') / 'notifications.json'
    yield


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
