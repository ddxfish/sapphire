"""The video avatar plugin's Sapphire side: whose clips, which names she is offered, and
what her prompt is told. Its modules are loaded by path under names of their own, with
the clips folder pointed at a temp dir: the real install is never read."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "user" / "plugins" / "vidavatar"
pytestmark = pytest.mark.skipif(not (PLUGIN / "lib.py").exists(), reason="the video avatar plugin is not here")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def lib(monkeypatch, tmp_path):
    mod = _load("vidavatar_lib_under_test", PLUGIN / "lib.py")
    monkeypatch.setattr(mod, "CLIPS", tmp_path)
    monkeypatch.setattr(mod, "settings", lambda: {})
    mod._names.clear()
    return mod


def clips(tmp_path, persona, *files):
    d = tmp_path / persona / "clips"
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        (d / f).write_bytes(b"x")
    return d


def test_a_name_is_made_a_folder_a_file_stem_and_a_tag(lib):
    assert lib.slug("  My Persona! ") == "my_persona"
    assert lib.slug("Wave-Hello 2") == "wave_hello_2"
    assert lib.slug("../../etc/passwd") == "etc_passwd"       # never a path
    assert lib.slug(None) == ""


def test_takes_fold_into_one_name_and_idle_is_not_offered(lib, tmp_path):
    clips(tmp_path, "sapphire", "idle.mp4", "idle_2.mp4", "wave.mp4", "wave_2.mp4", "wave_3.webm", "Happy Dance.mp4", "notes.txt")
    assert lib.names("sapphire") == ["happy_dance", "wave"]
    assert lib.names("nobody") == []


def test_the_list_follows_the_folder(lib, tmp_path):
    d = clips(tmp_path, "sapphire", "wave.mp4")
    assert lib.names("sapphire") == ["wave"]
    (d / "bow.mp4").write_bytes(b"x")
    import os
    os.utime(d, ns=(d.stat().st_atime_ns, d.stat().st_mtime_ns + 10 ** 9))      # a coarse clock would hide the change
    assert lib.names("sapphire") == ["bow", "wave"]


def test_a_pinned_persona_wins_over_the_chat(lib, monkeypatch):
    assert lib.persona_of("any chat", s={"persona": "Ash Prime"}) == "ash_prime"


@pytest.fixture
def prompt(lib, monkeypatch):
    pkg = types.ModuleType("plugins.vidavatar")
    pkg.lib = lib
    if "plugins" not in sys.modules:
        monkeypatch.setitem(sys.modules, "plugins", types.ModuleType("plugins"))
    monkeypatch.setitem(sys.modules, "plugins.vidavatar", pkg)
    return _load("vidavatar_prompt_under_test", PLUGIN / "hooks" / "prompt_inject.py")


def test_she_is_told_her_animations_only_when_this_chats_persona_has_clips(prompt, lib, tmp_path, monkeypatch):
    clips(tmp_path, "sapphire", "idle.mp4", "wave.mp4", "dance.mp4")
    monkeypatch.setattr(lib, "persona_of", lambda chat=None, s=None: {"hers": "sapphire"}.get(chat, "rook"))
    ev = types.SimpleNamespace(chat_name="hers", context_parts=[])
    prompt.prompt_inject(ev)
    assert len(ev.context_parts) == 1
    text = ev.context_parts[0]
    assert "<<avatar: name>>" in text and "dance, wave" in text and "idle" not in text.split("list")[1]
    ev2 = types.SimpleNamespace(chat_name="someone else's", context_parts=[])
    prompt.prompt_inject(ev2)
    assert ev2.context_parts == []                  # no clips for that persona: she is told nothing


def test_her_prompt_holds_still_between_turns(prompt, lib, tmp_path, monkeypatch):
    clips(tmp_path, "sapphire", "wave.mp4", "bow.mp4")
    monkeypatch.setattr(lib, "persona_of", lambda chat=None, s=None: "sapphire")
    said = []
    for _ in range(3):
        ev = types.SimpleNamespace(chat_name="c", context_parts=[])
        prompt.prompt_inject(ev)
        said.append(ev.context_parts[0])
    assert said[0] == said[1] == said[2]            # the same bytes: the prompt cache is not disturbed
