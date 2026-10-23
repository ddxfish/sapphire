"""Image upgrade, half A (2026-09-10, record tmp/image-upgrade.md §11-12).

Pins: core.images.stash() is the ONE lane (a tool_images row, visible=0,
owned by the EFFECTIVE chat; the bounded disk lane without a session; ''
refuses), _save_tool_image writes visible = not display_only, the orphan
prune keeps img: handles alive on rows AND blob chats (stashed images have no
UI marker — only the handle), the visible column lands on a pre-column
database, and the contact-sheet badge is a real scaled font.
"""
import base64
import io
import json
import re
import sqlite3
from unittest.mock import MagicMock

import pytest
from PIL import Image


def _png(w=40, h=30):
    b = io.BytesIO()
    Image.new('RGB', (w, h), (200, 30, 30)).save(b, 'PNG')
    return b.getvalue()


@pytest.fixture
def sm(tmp_path):
    """A manager on a TEMP history dir — the db AND the .active_chat marker
    (constructing on the default dir writes both into the live user/history)."""
    from core.chat.history import ChatSessionManager
    m = ChatSessionManager(history_dir=str(tmp_path))
    m.create_chat('pics')
    m.set_active_chat('pics')
    return m


def _ids(sm, chat='pics'):
    with sqlite3.connect(sm._db_path) as conn:
        return {r[0] for r in conn.execute("SELECT id FROM tool_images WHERE chat_name = ?", (chat,))}


# ── stash ────────────────────────────────────────────────────────────────────

def test_stash_hidden_row_under_effective_chat(sm, monkeypatch):
    from core import images as ci
    monkeypatch.setattr(ci, '_session_manager', lambda: sm)
    monkeypatch.setattr(sm, '_effective_chat_name', lambda: 'pics')
    handle = ci.stash(_png())
    assert re.fullmatch(r'img:[0-9a-f]{12}\.png', handle)
    with sqlite3.connect(sm._db_path) as conn:
        row = conn.execute("SELECT chat_name, media_type, visible FROM tool_images WHERE id = ?",
                           (handle[4:],)).fetchone()
    assert row == ('pics', 'image/png', 0)
    assert ci.resolve(handle).origin == 'chat'          # the handle round-trips


def test_stash_visible_and_explicit_owner(sm, monkeypatch):
    from core import images as ci
    monkeypatch.setattr(ci, '_session_manager', lambda: sm)
    sm.create_chat('other')
    handle = ci.stash(_png(), 'image/png', visible=True, chat_name='other')
    with sqlite3.connect(sm._db_path) as conn:
        row = conn.execute("SELECT chat_name, visible FROM tool_images WHERE id = ?",
                           (handle[4:],)).fetchone()
    assert row == ('other', 1)


def test_stash_ephemeral_turn_refuses(sm, monkeypatch):
    from core import images as ci
    monkeypatch.setattr(ci, '_session_manager', lambda: sm)
    with pytest.raises(ci.ImageError):
        ci.stash(_png(), chat_name='')      # '' = no durable home (a cadence frame)
    assert _ids(sm) == set()


def test_stash_without_session_uses_disk_lane(monkeypatch, tmp_path):
    from core import images as ci
    monkeypatch.setattr(ci, '_DISK', tmp_path / 'tool_images')

    def boom():
        raise RuntimeError('no system')
    monkeypatch.setattr(ci, '_session_manager', boom)
    handle = ci.stash(_png())
    assert (tmp_path / 'tool_images' / handle[4:]).is_file()
    assert ci.resolve(handle).origin == 'chat'


# ── the visible flag on the tool-return lane ─────────────────────────────────

@pytest.mark.parametrize('display_only,visible', [(False, True), (True, False)])
def test_tool_images_carry_visible(display_only, visible):
    from core.chat.chat_tool_calling import _save_tool_image
    history = MagicMock()
    history._effective_chat_name.return_value = 'pics'
    img = {"data": base64.b64encode(b'x').decode(), "media_type": "image/jpeg",
           "display_only": display_only}
    assert _save_tool_image(img, history)
    kw = history.save_tool_image.call_args.kwargs
    assert kw['chat_name'] == 'pics' and kw['visible'] is visible


# ── orphan prune keeps img: handles ──────────────────────────────────────────

def test_live_image_ids_regex():
    from core.chat.history import _live_image_ids
    text = ('(image img:ab12.jpg) then img:cd34.png. Marker "handle":"img:ef56.jpg", '
            '<<IMG::tool:gh78.jpg>> and img:x')
    assert _live_image_ids(text) == {'ab12.jpg', 'cd34.png', 'ef56.jpg', 'gh78.jpg', 'x'}
    assert _live_image_ids('') == set() and _live_image_ids(None) == set()


def test_prune_keeps_handles_on_rows_chat(sm):
    for i in ('sheet.jpg', 'hit1.jpg', 'hit2.jpg', 'gone.jpg'):
        sm.save_tool_image(i, b'x', 'image/jpeg', chat_name='pics', visible=(i == 'sheet.jpg'))
    sm.add_user_message('find cats')
    sm.add_tool_result('c1', 'web_view_images',
                       '<<IMG::tool:sheet.jpg>>\n(image img:sheet.jpg)\n1. A cat — img:hit1.jpg.\n'
                       '<!--GALLERY:{"title":"cats","items":[{"handle":"img:hit2.jpg"}]}-->')
    assert sm._prune_orphaned_tool_images('pics') == 1
    assert _ids(sm) == {'sheet.jpg', 'hit1.jpg', 'hit2.jpg'}


def test_prune_keeps_handles_on_blob_chat(sm):
    msgs = json.dumps([{'role': 'tool', 'content': 'see img:keep.jpg and <<IMG::tool:mark.jpg>>'}])
    with sqlite3.connect(sm._db_path) as conn:
        for i in ('keep.jpg', 'mark.jpg', 'drop.jpg'):
            conn.execute("INSERT INTO tool_images (id, chat_name, data, media_type, created_at) "
                         "VALUES (?, ?, ?, ?, datetime('now'))", (i, 'pics', b'x', 'image/jpeg'))
        conn.execute("UPDATE chats SET messages = ?, storage_format = 'blob' WHERE name = 'pics'",
                     (msgs,))
    assert sm._prune_orphaned_tool_images('pics') == 1
    assert _ids(sm) == {'keep.jpg', 'mark.jpg'}


# ── schema ───────────────────────────────────────────────────────────────────

def test_visible_column_lands_on_pre_column_db(tmp_path):
    from core.chat.history import ChatSessionManager
    db = tmp_path / 'sapphire_history.db'
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE tool_images (id TEXT PRIMARY KEY, chat_name TEXT NOT NULL, "
                     "data BLOB NOT NULL, media_type TEXT NOT NULL DEFAULT 'image/jpeg', "
                     "created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO tool_images VALUES ('old.jpg', 'pics', X'00', 'image/jpeg', 't')")
    m = ChatSessionManager(history_dir=str(tmp_path))
    assert m._db_path == db
    m._init_db()        # idempotent — the guarded ALTER must not fire twice
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT visible FROM tool_images WHERE id = 'old.jpg'").fetchone() == (1,)


# ── contact sheet badge ──────────────────────────────────────────────────────

def test_badge_font_is_a_scaled_face():
    from core import images as ci
    l, t, r, b = ci._badge_font(60).getbbox('8')
    assert b - t >= 36          # the 6×8 bitmap fallback can't reach this


def test_contact_sheet_badge_scales_with_cell():
    from core import images as ci
    sheet = Image.open(io.BytesIO(ci.contact_sheet([_png()] * 2, cell=300)))
    # the badge box is black; the glyph inside is white — both must exist near the corner
    corner = sheet.crop((0, 0, 100, 100)).convert('L')
    px = corner.tobytes()
    assert any(v < 10 for v in px) and any(v > 240 for v in px)


# ═════════════════════════════════════════════════════════════════════════════
# Half B — the vision window + the pasted-image lane (2026-09-10)
# ═════════════════════════════════════════════════════════════════════════════

from core.chat.history import ConversationHistory, _replay_images, _REPLAY_NOTE  # noqa: E402


def _b64(b):
    return base64.b64encode(b).decode('ascii')


def _loader(store):
    return lambda i: store.get(i)


def _transcript():
    """3 user turns: a pasted image, a tool sheet, a plain turn."""
    h = ConversationHistory()
    h.add_user_message([{"type": "text", "text": "look"},
                        {"type": "image", "handle": "img:p1.jpg", "media_type": "image/jpeg"}])
    h.add_assistant_final("nice")
    h.add_user_message("find cats")
    h.messages.append({"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "web_view_images", "arguments": "{}"}}]})
    h.add_tool_result("c1", "web_view_images",
                      "<<IMG::tool:s1.jpg>>\n(image img:s1.jpg)\n1. cat — img:h1.jpg\n"
                      '<!--GALLERY:{"title":"cats","items":[{"handle":"img:h1.jpg"}]}-->')
    h.add_assistant_final("here are cats")
    h.add_user_message("thanks")
    h.add_assistant_final("welcome")
    return h


STORE = {'p1.jpg': (b'PASTED', 'image/jpeg'), 's1.jpg': (b'SHEET', 'image/jpeg')}   # h1 = stashed → absent


def test_window_off_is_text_only_with_receipts(monkeypatch):
    msgs = _transcript().get_messages_for_llm(image_window=0, image_loader=_loader(STORE))
    assert msgs[0] == {"role": "user", "content": "look\n\n(image img:p1.jpg)"}
    tool = next(m for m in msgs if m["role"] == "tool")
    assert '<<IMG' not in tool["content"] and 'GALLERY' not in tool["content"]
    assert '(image img:s1.jpg)' in tool["content"]
    assert all(not isinstance(m["content"], list) for m in msgs)
    assert not any(m["role"] == "user" and _REPLAY_NOTE in str(m["content"]) for m in msgs)


def test_window_replays_exactly_what_she_saw():
    msgs = _transcript().get_messages_for_llm(image_window=3, image_loader=_loader(STORE))
    # pasted image rides its own row
    assert msgs[0]["content"][0] == {"type": "text", "text": "look\n\n(image img:p1.jpg)"}
    assert msgs[0]["content"][1] == {"type": "image", "data": _b64(b'PASTED'), "media_type": "image/jpeg"}
    # the tool batch's sheet comes back as ONE pseudo-user message after the tool row
    i = next(k for k, m in enumerate(msgs) if m["role"] == "tool")
    replay = msgs[i + 1]
    assert replay["role"] == "user" and replay["content"][0]["text"] == _REPLAY_NOTE
    assert [b["data"] for b in replay["content"][1:]] == [_b64(b'SHEET')]     # h1 (stashed) stays out
    assert msgs[i + 2]["role"] == "assistant"
    roles = [m["role"] for m in msgs]
    assert roles == ['user', 'assistant', 'user', 'assistant', 'tool', 'user', 'assistant', 'user', 'assistant']


def test_window_counts_user_turns_from_the_end():
    msgs = _transcript().get_messages_for_llm(image_window=2, image_loader=_loader(STORE))
    assert isinstance(msgs[0]["content"], str)                    # turn 1 is outside the window
    assert any(m["role"] == "user" and isinstance(m["content"], list) for m in msgs)   # turn 2's replay
    msgs = _transcript().get_messages_for_llm(image_window=1, image_loader=_loader(STORE))
    assert all(not isinstance(m["content"], list) for m in msgs)  # turn 3 had no images


def test_window_needs_a_loader_and_skips_missing():
    msgs = _transcript().get_messages_for_llm(image_window=3, image_loader=None)
    assert all(not isinstance(m["content"], list) for m in msgs)
    msgs = _transcript().get_messages_for_llm(image_window=3, image_loader=_loader({}))
    assert all(not isinstance(m["content"], list) for m in msgs)  # nothing loadable → nothing attached


def test_replay_legacy_inline_row_and_empty_text():
    msgs = [{"role": "user", "content": ""}, {"role": "assistant", "content": "ok"}]
    refs = [[('inline', 'QUJD', 'image/png')], []]
    out = _replay_images(msgs, refs, 1, _loader({}))
    assert out[0]["content"] == [{"type": "image", "data": "QUJD", "media_type": "image/png"}]   # no empty text block


def test_window_images_cost_tokens_in_the_trim(monkeypatch):
    import core.chat.history as hist
    monkeypatch.setattr(hist.config, 'LLM_MAX_HISTORY', 0, raising=False)
    h = _transcript()
    wide = h.get_messages_for_llm(image_window=3, image_loader=_loader(STORE), context_limit=100000)
    tight = h.get_messages_for_llm(image_window=3, image_loader=_loader(STORE), context_limit=3600)
    assert len(tight) < len(wide)                                  # ~3k of images evicted older text
    assert any(isinstance(m["content"], list) for m in tight)      # the newest images survive


def test_manager_passes_the_setting_and_visible_only(sm, monkeypatch):
    import core.chat.history as hist
    sm.save_tool_image('seen.jpg', b'SEEN', 'image/jpeg', chat_name='pics', visible=True)
    sm.save_tool_image('hid.jpg', b'HID', 'image/jpeg', chat_name='pics', visible=False)
    assert sm.visible_tool_image('seen.jpg') == (b'SEEN', 'image/jpeg')
    assert sm.visible_tool_image('hid.jpg') is None and sm.visible_tool_image('nope.jpg') is None
    sm.add_user_message([{"type": "text", "text": "pasted"},
                         {"type": "image", "handle": "img:seen.jpg", "media_type": "image/jpeg"}])
    sm.add_assistant_final("ok")
    monkeypatch.setattr(hist.config, 'IMAGE_MEMORY_TURNS', 3, raising=False)
    monkeypatch.setattr(hist.config, 'LLM_MAX_HISTORY', 0, raising=False)
    msgs = sm.get_messages_for_llm()
    assert msgs[0]["content"][1]["data"] == _b64(b'SEEN')
    monkeypatch.setattr(hist.config, 'IMAGE_MEMORY_TURNS', 0, raising=False)
    assert isinstance(sm.get_messages_for_llm()[0]["content"], str)


def test_replay_shape_survives_both_provider_families():
    from core.chat.llm_providers.anthropic_compat import AnthropicCompatProvider
    from core.chat.llm_providers.openai_compat import OpenAICompatProvider
    msgs = _transcript().get_messages_for_llm(image_window=3, image_loader=_loader(STORE))
    claude = AnthropicCompatProvider.__new__(AnthropicCompatProvider)
    claude.config = {"supports_images": True}
    _sys, out = claude._convert_messages(msgs)
    srcs = [b for m in out for b in (m["content"] if isinstance(m["content"], list) else [])
            if b.get("type") == "image"]
    assert len(srcs) == 2 and all(b["source"]["type"] == "base64" for b in srcs)
    claude.config = {"supports_images": False}
    _sys, out = claude._convert_messages(msgs)
    assert not [b for m in out for b in (m["content"] if isinstance(m["content"], list) else [])
                if b.get("type") == "image"]                       # text-only provider strips them
    cfg = {"base_url": "http://x/v1", "api_key": "t", "model": "m", "timeout": 5, "enabled": True}
    oa = OpenAICompatProvider(dict(cfg, supports_images=True))
    out = oa._sanitize_messages(msgs)
    urls = [b for m in out for b in (m["content"] if isinstance(m["content"], list) else [])
            if b.get("type") == "image_url"]
    assert len(urls) == 2 and urls[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    # the vision checkbox OFF: no image_url anywhere (a text-only model 400s on one), placeholder text instead
    out = OpenAICompatProvider(dict(cfg, supports_images=False))._sanitize_messages(msgs)
    assert all(isinstance(m["content"], str) for m in out)
    assert sum('[image not sent' in m["content"] for m in out) == 2


# ── pasted lane ──────────────────────────────────────────────────────────────

def test_stash_pasted_handles_and_inline_fallback(monkeypatch):
    from core import images as ci
    from core.chat.chat_streaming import _stash_pasted
    calls = []

    def fake(raw, media_type=None, *, visible=False, chat_name=None):
        calls.append((raw, visible))
        if raw == b'BAD':
            raise ci.ImageError('nope')
        return 'img:ok.jpg'
    monkeypatch.setattr(ci, 'stash', fake)
    out = _stash_pasted([{"data": _b64(b'GOOD')}, {"data": _b64(b'BAD')}])
    assert out == ['img:ok.jpg', None] and calls == [(b'GOOD', True), (b'BAD', True)]


def test_prune_keeps_a_pasted_row_alive(sm):
    sm.save_tool_image('p9.jpg', b'x', 'image/jpeg', chat_name='pics', visible=True)
    sm.save_tool_image('lost.jpg', b'x', 'image/jpeg', chat_name='pics', visible=True)
    sm.add_user_message([{"type": "text", "text": "see"},
                         {"type": "image", "handle": "img:p9.jpg", "media_type": "image/jpeg"}])
    assert sm._prune_orphaned_tool_images('pics') == 1
    assert _ids(sm) == {'p9.jpg'}


def test_display_formatter_emits_stored_ids():
    from core.chat.display_format import format_messages_for_display
    rows = [{"role": "user", "timestamp": 1, "content": [
        {"type": "text", "text": "see"},
        {"type": "image", "handle": "img:p9.jpg", "media_type": "image/jpeg"},
        {"type": "image", "data": "QUJD", "media_type": "image/png"}]}]
    out = format_messages_for_display(rows)
    assert out[0]["images"] == [{"media_type": "image/jpeg", "id": "p9.jpg"},
                                {"media_type": "image/png", "data": "QUJD"}]


def test_responses_provider_honours_the_vision_checkbox():
    from core.chat.llm_providers.openai_responses import OpenAIResponsesProvider
    msgs = _transcript().get_messages_for_llm(image_window=3, image_loader=_loader(STORE))
    cfg = {"base_url": "https://api.openai.com/v1", "api_key": "t", "model": "gpt-x", "timeout": 5, "enabled": True}
    on = OpenAIResponsesProvider(dict(cfg, supports_images=True))._convert_messages_to_input(msgs)
    kinds = [c["type"] for it in on if it.get("type") == "message" and isinstance(it.get("content"), list) for c in it["content"]]
    assert kinds.count("input_image") == 2
    off = OpenAIResponsesProvider(dict(cfg, supports_images=False))._convert_messages_to_input(msgs)
    texts = [c for it in off if it.get("type") == "message" and isinstance(it.get("content"), list) for c in it["content"]]
    assert not [c for c in texts if c["type"] == "input_image"]
    assert sum('[image not sent' in c.get("text", "") for c in texts) == 2
