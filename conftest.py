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


# Her real data is never a test fixture (2026-11-14). The full suite was found
# writing the dev box's real install on every run: user/history/.active_chat
# (a ChatSessionManager() built on the default folder also OPENS the real
# history database), user/settings.json (read and written back about 25 times
# by one fixture), and a 150 MB "manual" backup in user_backups/ whose rotation
# can delete a backup the person made. These refuse at the class, for the whole
# process and from import time, so a fresh instance, the module singleton and a
# thread that outlives its test's monkeypatch all meet the same refusal, and
# every attempt is listed at the end of the run with the test that made it.
import os

_REAL = Path(__file__).parent.resolve()
_LEAKS = []


def _same(a, b):
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, TypeError):
        return False


def _refuse(what):
    line = f"{what}  [{os.environ.get('PYTEST_CURRENT_TEST', 'no test running: collection or a stray thread')}]"
    _LEAKS.append(line)
    raise RuntimeError(f"a test reached the real install: {line}. Point it at tmp_path.")


try:
    from core.chat import history as _hist_mod
    _real_history_init = _hist_mod.ChatSessionManager.__init__

    def _guarded_history_init(self, *a, **k):
        folder = Path(k.get('history_dir', a[1] if len(a) > 1 else "user/history"))
        if _same(folder if folder.is_absolute() else _REAL / folder, _REAL / "user" / "history"):
            _refuse("ChatSessionManager() on the real user/history (pass history_dir=str(tmp_path))")
        return _real_history_init(self, *a, **k)

    _hist_mod.ChatSessionManager.__init__ = _guarded_history_init
except Exception:
    pass

try:
    from core import settings_manager as _sm_mod

    def _guard_settings(name):
        real = getattr(_sm_mod.SettingsManager, name)

        def guarded(self, *a, **k):
            if _same(Path(self.BASE_DIR) / 'user' / 'settings.json', _REAL / 'user' / 'settings.json'):
                _refuse(f"SettingsManager.{name} would write the real user/settings.json")
            return real(self, *a, **k)
        setattr(_sm_mod.SettingsManager, name, guarded)

    for _name in ('save', '_remove_key_from_file', 'reset_to_defaults'):
        _guard_settings(_name)
except Exception:
    pass

try:
    from core import backup as _bk_mod

    def _guard_backup(name):
        real = getattr(_bk_mod.Backup, name)

        def guarded(self, *a, **k):
            dest = (k.get('dest_dir') or (a[2] if len(a) > 2 else None)) if name == 'create_backup' else None
            if _same(dest or self.backup_dir, _REAL / 'user_backups'):
                _refuse(f"Backup.{name} in the real user_backups/")
            if name == 'create_backup' and _same(self.user_dir, _REAL / 'user'):
                _refuse("Backup.create_backup of the real user/ folder")
            return real(self, *a, **k)
        setattr(_bk_mod.Backup, name, guarded)

    for _name in ('create_backup', 'rotate_backups', 'delete_backup'):
        _guard_backup(_name)
except Exception:
    pass


# Her personas and the new-chat defaults (2026-11-16): the persona manager is
# one singleton on the real user/personas/personas.json, and a scope delete now
# releases the personas that held the scope (core/referrers.py scope_deleted).
# A test that deletes a scope a real persona happens to hold would have
# rewritten her file. Reading stays allowed; a write is refused.
try:
    from core.personas import persona_manager as _pm_singleton     # the package exports the INSTANCE under the module's name
    _PersonaManager = type(_pm_singleton)
    _real_persona_save = _PersonaManager._save_to_user

    def _guarded_persona_save(self, *a, **k):
        if _same(Path(self.USER_DIR) / 'personas.json', _REAL / 'user' / 'personas' / 'personas.json'):
            _refuse("PersonaManager._save_to_user would write the real user/personas/personas.json")
        return _real_persona_save(self, *a, **k)

    _PersonaManager._save_to_user = _guarded_persona_save
except Exception:
    pass

try:
    import json as _json
    _real_repoint_defaults = _hist_mod.repoint_chat_defaults

    def _guarded_repoint_defaults(key, old, new):
        path = _hist_mod._user_chat_defaults_path()
        if _same(path, _REAL / 'user' / 'settings' / 'chat_defaults.json'):
            try:
                held = _json.loads(path.read_text(encoding='utf-8-sig')).get(key) == old
            except Exception:
                held = False
            if held:
                _refuse("repoint_chat_defaults would rewrite the real user/settings/chat_defaults.json")
            return False
        return _real_repoint_defaults(key, old, new)

    _hist_mod.repoint_chat_defaults = _guarded_repoint_defaults
except Exception:
    pass


@pytest.fixture(autouse=True)
def _real_personas_hold_no_scope(monkeypatch):
    """A scope delete releases the personas that held the scope, and any test that deletes
    a scope reaches the persona singleton, which is loaded from the real personas.json: a
    Mind Palace test deleting a scope one of her personas really holds changed it in memory
    and was refused at the write (the guard above, 2026-11-16). In tests the real singleton
    holds nothing; a test of the release builds its own manager."""
    try:
        monkeypatch.setattr(_pm_singleton, 'repoint_setting', lambda key, old, new: [], raising=False)
    except Exception:
        pass


def pytest_terminal_summary(terminalreporter):
    if _LEAKS:
        terminalreporter.section("REAL INSTALL REACHED BY TESTS (refused)")
        for line in dict.fromkeys(_LEAKS):
            terminalreporter.write_line(line)


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


@pytest.fixture(autouse=True)
def _no_real_toolset_repair(monkeypatch):
    """Toolset engine drift (core/toolsets/engine_drift.py) reads the dev box's
    real memory-engine.json and rewrites the real user/toolsets.json; the Mind
    Palace import runs it at the end of every run. In tests no engine runs
    unless a test says which (monkeypatch engine_drift._engine)."""
    try:
        from core.toolsets import engine_drift as _ed
    except Exception:
        return
    monkeypatch.setattr(_ed, '_engine', lambda: None)


class _AlwaysOld(dict):
    """Every key a board from before signing: it is sent Bearer, one call per request."""
    def get(self, k, default=None):
        return dict.get(self, k, float('inf'))


@pytest.fixture(autouse=True)
def _hermetic_satellite_signing(tmp_path, monkeypatch):
    """The satellite driver remembers in user/device_signed.json which boards sign; a fake
    board answering 200 would write the dev box's real file. Boards in tests take the key
    itself unless a test says otherwise (tests/test_devices_satellite_signed.py sets _old)."""
    try:
        from core.devices.drivers import satellite as _s
    except Exception:
        return
    monkeypatch.setattr(_s, 'SIGNED_FILE', tmp_path / 'device_signed.json')
    monkeypatch.setattr(_s, '_signed', set())
    monkeypatch.setattr(_s, '_old', _AlwaysOld())
    monkeypatch.setattr(_s, '_skew', {})
