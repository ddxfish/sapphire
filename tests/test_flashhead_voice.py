"""FlashHead's voice hook (hooks/voice.py): where each chunk of a reply went, and
the one line that says so. No server: the file is loaded by path beside a
stand-in `faces`, so the plugin's real modules are never touched."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent / "user" / "plugins" / "flashhead"
pytestmark = pytest.mark.skipif(not (PLUGIN / "hooks" / "voice.py").exists(), reason="FlashHead plugin not here")


@pytest.fixture
def voice(monkeypatch):
    posts, answers = [], []

    class Resp:
        ok = True
        def __init__(self, obj): self.obj = obj
        def json(self): return self.obj

    def post(url, **kw):
        posts.append(url.split("http://face")[1])
        got = answers.pop(0) if answers and "/speak" in url else {"ok": True}
        if isinstance(got, Exception):
            raise got
        return Resp(got)

    faces = types.SimpleNamespace(http=lambda: types.SimpleNamespace(post=post), base_url=lambda: "http://face",
                                  forget=lambda: None, worker=lambda: None)
    pkg = types.ModuleType("plugins.flashhead")
    pkg.faces = faces
    if "plugins" not in sys.modules:
        monkeypatch.setitem(sys.modules, "plugins", types.ModuleType("plugins"))
    monkeypatch.setitem(sys.modules, "plugins.flashhead", pkg)
    spec = importlib.util.spec_from_file_location("fh_voice_under_test", PLUGIN / "hooks" / "voice.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.posts, mod.answers = posts, answers
    mod._ready.update(ok=True, at=0.0)
    return mod


def _chunk(sid="reply-1"):
    return types.SimpleNamespace(skip_tts=False, chat_private=None,
                                 metadata={"stream_id": sid, "audio": {"audio_bytes": b"ogg"}})


def test_a_reply_the_server_left_half_way_is_counted_whole_and_the_face_is_told_it_ended(voice, caplog):
    """After a /speak that failed, the chunks that followed went home uncounted (the line said 1 played
    here of 3), and the face was never told the reply was over."""
    voice.answers += [{"taken": True}, ConnectionError("gone")]
    taken = []
    for _ in range(4):
        e = _chunk()
        voice.tts_chunk_audio(e)
        taken.append(e.skip_tts)
    assert taken == [True, False, False, False] and not voice._ready["ok"]
    with caplog.at_level("INFO"):
        voice.tts_stream_end(types.SimpleNamespace(metadata={"stream_id": "reply-1"}))
    line = next(r.message for r in caplog.records if "reply:" in r.message)
    assert "1 to the face, 3 played here (1 ConnectionError, 2 server gone)" in line
    assert voice.posts[-1] == "/end"


def test_a_reply_that_never_reached_the_face_says_nothing_and_posts_nothing(voice, caplog):
    voice._ready["ok"] = False
    voice.tts_chunk_audio(_chunk("reply-2"))
    with caplog.at_level("INFO"):
        voice.tts_stream_end(types.SimpleNamespace(metadata={"stream_id": "reply-2"}))
    assert not voice.posts and not any("reply:" in r.message for r in caplog.records) and not voice._tally
