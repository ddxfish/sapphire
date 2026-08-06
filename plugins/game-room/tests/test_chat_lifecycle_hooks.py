# chat_renamed / chat_deleted subscriber — the coverage test_save_paths.py's
# header promised and never had (post-fix review 2026-08-05).
#
# Contract under test (reworked same day):
#   - key discovery walks the DISK, never the installed-pack catalogue — a
#     disabled/blocked/uninstalled pack's saves still carry and archive
#   - a rename target that already exists is ARCHIVED, not silently skipped
#     (skipping handed the renamed chat a stranger's journal)
#   - legacy-named dirs carry under the LEGACY scheme so save_dir() adoption
#     still finds them afterward
#   - game saves are ARCHIVED on delete (Krem's ruling 2026-08-05), moved
#     under the session lock on rename
#
# gameroom_core is STUBBED: the real module binds the loader's PluginState
# singleton at import, which reads (and would write) the developer's real
# user/plugin_state/game-room.json.
import importlib.util
import json
import sys
import threading
import types
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from gameroom_story import rooms, state as st  # noqa: E402


def _load_lifecycle():
    spec = importlib.util.spec_from_file_location(
        "gr_test_lifecycle", PLUGIN_DIR / "hooks" / "lifecycle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Store:
    def __init__(self):
        self.data = {}

    def all(self):
        return dict(self.data)

    def get(self, k):
        return self.data.get(k)

    def save(self, k, v):
        self.data[k] = v

    def delete(self, k):
        self.data.pop(k, None)


@pytest.fixture
def gc_stub(monkeypatch):
    m = types.ModuleType("gameroom_core")
    m.store = _Store()
    m.state_key = lambda gid, session=None: (f"game:{gid}:{session}"
                                             if session else f"game:{gid}")
    _locks = {}

    def session_lock(gid, session=None):
        return _locks.setdefault((gid, session), threading.RLock())

    m.session_lock = session_lock
    monkeypatch.setitem(sys.modules, "gameroom_core", m)
    return m


def _event(**metadata):
    return types.SimpleNamespace(metadata=metadata)


def _mk_journal(chat_dir, lines):
    chat_dir.mkdir(parents=True, exist_ok=True)
    (chat_dir / "journal.jsonl").write_text(
        "".join(json.dumps({"event": "started", "n": i}) + "\n"
                for i in range(lines)), encoding="utf-8")


def _journal_lines(chat_dir):
    p = chat_dir / "journal.jsonl"
    return len(p.read_text(encoding="utf-8").splitlines()) if p.exists() else 0


def test_rename_carries_disk_dirs_without_catalogue(gc_stub):
    """The story dir has NO story.json — an uninstalled/disabled pack's save.
    The catalogue-based walk left exactly this behind (reproduced finding)."""
    lc = _load_lifecycle()
    story_dir = rooms.SAVES_ROOT / rooms._safe("titanic")
    src = story_dir / rooms._safe("old chat")
    _mk_journal(src, 7)

    lc.chat_renamed(_event(old="old chat", new="new chat"))

    dst = story_dir / rooms._safe("new chat")
    assert not src.exists()
    assert _journal_lines(dst) == 7


def test_rename_archives_existing_target_never_skips(gc_stub):
    """Pre-hook residue at the target name is archived; the renamed chat gets
    its OWN journal, never a stranger's (reproduced finding: the old
    `not dst.exists()` guard silently skipped the whole carry)."""
    lc = _load_lifecycle()
    story_dir = rooms.SAVES_ROOT / rooms._safe("titanic")
    src = story_dir / rooms._safe("victim")
    dst = story_dir / rooms._safe("ghosted")
    _mk_journal(src, 7)
    _mk_journal(dst, 99)          # a stranger's pre-existing run

    lc.chat_renamed(_event(old="victim", new="ghosted"))

    assert _journal_lines(dst) == 7, "renamed chat must carry its own run"
    archived = list(story_dir.glob(f"_deleted-{rooms._safe('ghosted')}*"))
    assert len(archived) == 1, "the stranger's run must be archived, not lost"
    assert _journal_lines(archived[0]) == 99


def test_rename_carries_legacy_dirs_under_legacy_scheme(gc_stub):
    """A legacy-named dir moves as a legacy name, so save_dir()'s adoption
    path still finds and adopts it for the new chat afterward."""
    lc = _load_lifecycle()
    legacy_story = rooms.SAVES_ROOT / rooms._legacy_safe("titanic")
    src = legacy_story / rooms._legacy_safe("old chat")
    _mk_journal(src, 3)

    lc.chat_renamed(_event(old="old chat", new="new chat"))

    moved = legacy_story / rooms._legacy_safe("new chat")
    assert not src.exists()
    assert _journal_lines(moved) == 3
    # ...and adoption picks it up as THE dir for (titanic, new chat)
    adopted = rooms.save_dir("titanic", "new chat")
    assert _journal_lines(adopted) == 3


def test_rename_carries_game_saves(gc_stub):
    lc = _load_lifecycle()
    gc_stub.store.save("game:holdem:poker_night", {"pot": 500})
    gc_stub.store.save("game:holdem:other", {"pot": 1})

    lc.chat_renamed(_event(old="poker_night", new="poker_aug5"))

    assert gc_stub.store.get("game:holdem:poker_night") is None
    assert gc_stub.store.get("game:holdem:poker_aug5") == {"pot": 500}
    assert gc_stub.store.get("game:holdem:other") == {"pot": 1}


def test_delete_archives_everything_and_clears_pointer(gc_stub):
    """Journals AND game saves archive (never erase); the active pointer goes
    first so a recreated same-name chat starts clean."""
    lc = _load_lifecycle()
    story_dir = rooms.SAVES_ROOT / rooms._safe("titanic")
    src = story_dir / rooms._safe("doomed")
    _mk_journal(src, 5)
    st.set_active("doomed", "titanic", "sapphire")
    gc_stub.store.save("game:towerd:doomed", {"wave": 40})

    lc.chat_deleted(_event(name="doomed"))

    assert "doomed" not in st.get_active()
    assert not src.exists()
    archived = list(story_dir.glob(f"_deleted-{rooms._safe('doomed')}*"))
    assert len(archived) == 1 and _journal_lines(archived[0]) == 5
    assert gc_stub.store.get("game:towerd:doomed") is None
    assert gc_stub.store.get("_deleted:game:towerd:doomed") == {"wave": 40}
