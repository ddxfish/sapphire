"""Plugin surfaces (2026-08-23): a plugin's manifest `surfaces` names where
its PRESENCE injections belong; the runner withholds prompt_inject /
ghost_inject from plugins whose surfaces exclude the turn's surface. The
user's per-plugin override (plugin state `surfaces`) wins over the
manifest. Old plugins (no declaration) fire everywhere.

Run with: pytest tests/test_plugin_surfaces.py -v
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.hooks import HookEvent, HookRunner, SURFACES, SURFACE_HOOKS  # noqa: E402


@pytest.fixture
def runner():
    return HookRunner()


def _tag(name):
    def h(event):
        event.context_parts.append(name)
    return h


def test_declared_plugin_withheld_off_surface(runner):
    runner.register("prompt_inject", _tag("avatar"), plugin_name="avatar")
    runner.set_surfaces("avatar", ["chat"])
    ev = HookEvent(surface="game", chat_private=False)
    runner.fire("prompt_inject", ev)
    assert ev.context_parts == []
    ev = HookEvent(surface="chat", chat_private=False)
    runner.fire("prompt_inject", ev)
    assert ev.context_parts == ["avatar"]


def test_undeclared_plugin_fires_everywhere(runner):
    runner.register("prompt_inject", _tag("weather"), plugin_name="weather")
    for surf in ("chat", "game", None):
        ev = HookEvent(surface=surf, chat_private=False)
        runner.fire("prompt_inject", ev)
        assert ev.context_parts == ["weather"], surf


def test_unknown_surface_delivers_to_all(runner):
    # None = the fire site didn't say → never withhold on a guess
    runner.register("prompt_inject", _tag("avatar"), plugin_name="avatar")
    runner.set_surfaces("avatar", ["chat"])
    ev = HookEvent(surface=None, chat_private=False)
    runner.fire("prompt_inject", ev)
    assert ev.context_parts == ["avatar"]


def test_only_presence_hooks_are_filtered(runner):
    seen = []
    runner.register("pre_chat", lambda e: seen.append("pre"), plugin_name="avatar")
    runner.register("ghost_inject", lambda e: setattr(e, "ghost_text", "g"), plugin_name="avatar")
    runner.set_surfaces("avatar", ["chat"])
    ev = HookEvent(surface="game", chat_private=False)
    runner.fire("pre_chat", ev)
    assert seen == ["pre"]                      # not a presence hook
    ev = HookEvent(surface="game", chat_private=False)
    runner.fire("ghost_inject", ev)
    assert ev.ghost_contributions == []         # presence hook, withheld
    assert SURFACE_HOOKS == {"prompt_inject", "ghost_inject"}


def test_empty_list_hides_everywhere_and_clear_restores(runner):
    runner.register("prompt_inject", _tag("avatar"), plugin_name="avatar")
    runner.set_surfaces("avatar", [])
    ev = HookEvent(surface="chat", chat_private=False)
    runner.fire("prompt_inject", ev)
    assert ev.context_parts == []
    runner.set_surfaces("avatar", None)
    ev = HookEvent(surface="chat", chat_private=False)
    runner.fire("prompt_inject", ev)
    assert ev.context_parts == ["avatar"]
    assert runner.surfaces_of("avatar") is None


def test_unregister_plugin_drops_surfaces(runner):
    runner.register("prompt_inject", _tag("avatar"), plugin_name="avatar")
    runner.set_surfaces("avatar", ["chat"])
    runner.unregister_plugin("avatar")
    assert runner.surfaces_of("avatar") is None


def test_effective_surfaces_override_wins(monkeypatch, tmp_path):
    from core import plugin_loader as pl
    monkeypatch.setattr(pl, "PLUGIN_STATE_DIR", tmp_path)
    loader = pl.plugin_loader
    # a fresh state object bound to the temp dir (the cache is per-name)
    with loader._plugin_state_cache_lock:
        loader._plugin_state_cache.pop("surf-test", None)
    manifest = {"surfaces": ["chat"]}
    assert loader.effective_surfaces("surf-test", manifest) == ["chat"]
    # user override (Plugins page) → wins; unknown ids dropped
    loader.get_plugin_state("surf-test").save("surfaces", ["chat", "game", "bogus"])
    assert loader.effective_surfaces("surf-test", manifest) == ["chat", "game"]
    # back to default
    loader.get_plugin_state("surf-test").delete("surfaces")
    assert loader.effective_surfaces("surf-test", manifest) == ["chat"]
    # no declaration → None (everywhere), override or not
    loader.get_plugin_state("surf-test").save("surfaces", ["game"])
    assert loader.effective_surfaces("surf-test", {}) is None
    with loader._plugin_state_cache_lock:
        loader._plugin_state_cache.pop("surf-test", None)


def test_known_surfaces():
    ids = [s for s, _ in SURFACES]
    assert ids == ["chat", "game"]
