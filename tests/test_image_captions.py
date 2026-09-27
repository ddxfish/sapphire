"""The caption lives on the image (2026-09-26).

tool_images.caption is read and written through the session manager;
core.image_describe.for_image describes a stored image ONCE; caption_blind
hands a model with no vision the words in place of every stored image on the
wire (pasted this turn, or replayed by the vision window) and leaves blocks
with no id (perception frames) blind. The describer's picker lists every
enabled model line; being picked forces vision on for that one call.
"""
import base64
import re
import sqlite3
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from core import image_describe as d
from core.chat.history import ConversationHistory, _REPLAY_NOTE

ROOT = Path(__file__).resolve().parents[1]
BLIND = SimpleNamespace(supports_images=False)
SEES = SimpleNamespace(supports_images=True)


def _b64(b):
    return base64.b64encode(b).decode('ascii')


def _png():
    out = BytesIO()
    Image.new('RGB', (8, 8), (200, 30, 30)).save(out, format='PNG')
    return out.getvalue()


class Store:
    """The three store calls the describer uses."""

    def __init__(self, images=None):
        self.images = dict(images or {})
        self.captions = {}

    def image_caption(self, image_id):
        return self.captions.get(image_id)

    def set_image_caption(self, image_id, text):
        if image_id not in self.images:
            return False
        self.captions[image_id] = text
        return True

    def get_tool_image(self, image_id):
        return self.images.get(image_id)


@pytest.fixture
def described(monkeypatch):
    calls = []

    def fake(raw, private=None):
        calls.append((raw, private))
        return f"Image: {raw.decode('ascii') if len(raw) < 20 else 'picture'} in words."
    monkeypatch.setattr(d, 'describe', fake)
    return calls


@pytest.fixture
def sm(tmp_path):
    from core.chat.history import ChatSessionManager
    m = ChatSessionManager(history_dir=str(tmp_path))
    m.create_chat('pics')
    m.set_active_chat('pics')
    return m


def _pasted(image_id, raw, text='look'):
    return {'role': 'user', 'content': [
        {'type': 'text', 'text': f'{text}\n(image img:{image_id})'},
        {'type': 'image', 'data': _b64(raw), 'media_type': 'image/png', 'id': image_id}]}


# ── the store ────────────────────────────────────────────────────────────────

def test_caption_is_stored_on_the_image_row_and_dies_with_it(sm):
    assert sm.save_tool_image('a.png', b'PIXELS', 'image/png', chat_name='pics')
    assert sm.image_caption('a.png') is None
    assert sm.set_image_caption('a.png', '  A red barn.  ') is True
    assert sm.image_caption('a.png') == 'A red barn.'
    assert sm.set_image_caption('missing.png', 'x') is False
    assert sm.set_image_caption('a.png', '   ') is False and sm.image_caption('a.png') == 'A red barn.'
    with sqlite3.connect(sm._db_path) as conn:
        conn.execute("DELETE FROM tool_images WHERE id = 'a.png'")
        conn.commit()
    assert sm.image_caption('a.png') is None


def test_caption_column_lands_on_a_database_that_predates_it(tmp_path):
    from core.chat.history import ChatSessionManager
    m = ChatSessionManager(history_dir=str(tmp_path))
    m.create_chat('pics')
    assert m.save_tool_image('a.png', b'PIXELS', 'image/png', chat_name='pics')
    with sqlite3.connect(m._db_path) as conn:
        conn.execute("ALTER TABLE tool_images DROP COLUMN caption")
        conn.commit()
        assert 'caption' not in {r[1] for r in conn.execute("PRAGMA table_info(tool_images)")}

    m2 = ChatSessionManager(history_dir=str(tmp_path))
    m2._ensure_db()

    with sqlite3.connect(m2._db_path) as conn:
        assert 'caption' in {r[1] for r in conn.execute("PRAGMA table_info(tool_images)")}
    assert m2.get_tool_image('a.png')[0] == b'PIXELS'            # the row survived the migration
    assert m2.set_image_caption('a.png', 'kept') and m2.image_caption('a.png') == 'kept'


# ── described once ───────────────────────────────────────────────────────────

def test_a_stored_image_is_described_once(described):
    store = Store({'a.png': (b'barn', 'image/png')})
    assert d.for_image('a.png', b'barn', store=store) == 'Image: barn in words.'
    assert d.for_image('a.png', _b64(b'barn'), store=store) == 'Image: barn in words.'
    assert d.for_image('a.png', store=store) == 'Image: barn in words.'
    assert len(described) == 1 and store.captions == {'a.png': 'Image: barn in words.'}
    lake = Store({'b.png': (b'lake', 'image/png')})
    assert d.for_image('b.png', store=lake) == 'Image: lake in words.'        # the store supplies the pixels
    assert d.for_image('', b'sky', store=lake) == 'Image: sky in words.'      # no id: described, nothing kept
    assert lake.captions == {'b.png': 'Image: lake in words.'}
    assert d.for_image('gone.png', store=lake) == ''                          # no pixels anywhere


# ── the blind pass ───────────────────────────────────────────────────────────

def test_blind_model_reads_a_pasted_image_in_words(described):
    store = Store({'p1.png': (b'barn', 'image/png')})
    messages = [{'role': 'system', 'content': 'sys'}, _pasted('p1.png', b'barn')]

    assert d.caption_blind(messages, BLIND, store=store) == 1

    assert messages[1]['content'] == 'look\n(image img:p1.png)\n\nImage: barn in words.'
    assert store.captions['p1.png'] == 'Image: barn in words.'


def test_perception_frames_and_seeing_models_are_left_alone(described):
    frame = {'type': 'image', 'data': _b64(b'frame'), 'media_type': 'image/jpeg'}     # no id: never stored
    messages = [{'role': 'user', 'content': [{'type': 'text', 'text': 'your move'}, dict(frame)]}]
    assert d.caption_blind(messages, BLIND, store=Store()) == 0
    assert messages[0]['content'][1] == frame

    stored = [_pasted('p1.png', b'barn')]
    assert d.caption_blind(stored, SEES, store=Store({'p1.png': (b'barn', 'image/png')})) == 0
    assert stored[0]['content'][1]['type'] == 'image'
    assert described == []


def _history(tool_text):
    h = ConversationHistory()
    h.add_user_message([{'type': 'text', 'text': 'look'},
                        {'type': 'image', 'handle': 'img:p1.jpg', 'media_type': 'image/jpeg'}])
    h.add_assistant_final('nice')
    h.add_user_message('find cats')
    h.messages.append({'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'c1', 'type': 'function', 'function': {'name': 'web_view_images', 'arguments': '{}'}}]})
    h.add_tool_result('c1', 'web_view_images', tool_text)
    h.add_assistant_final('here')
    return h


def test_replay_carries_the_caption_and_never_says_it_twice(described):
    store = Store({'p1.jpg': (b'barn', 'image/jpeg'), 's1.jpg': (b'sheet', 'image/jpeg')})
    store.captions.update({'p1.jpg': 'Image: barn in words.', 's1.jpg': 'Image: sheet in words.'})
    h = _history('<<IMG::tool:s1.jpg>>\n(image img:s1.jpg)\ncats\n\nImage: sheet in words.')

    msgs = h.get_messages_for_llm(image_window=3, image_loader=store.get_tool_image,
                                  caption_loader=store.image_caption)

    assert msgs[0]['content'][0]['text'] == 'look\n\n(image img:p1.jpg)\nImage: barn in words.'
    assert msgs[0]['content'][1]['id'] == 'p1.jpg'
    assert any(isinstance(m['content'], list) and m['content'][0].get('text') == _REPLAY_NOTE for m in msgs)

    assert d.caption_blind(msgs, BLIND, store=store) == 2

    assert described == []                                                  # both came off the rows
    assert msgs[0]['content'] == 'look\n\n(image img:p1.jpg)\nImage: barn in words.'
    assert all(not isinstance(m['content'], list) for m in msgs)            # no image block left
    assert not any(_REPLAY_NOTE in str(m['content']) for m in msgs)         # the empty replay note is gone
    assert sum(str(m['content']).count('Image: sheet in words.') for m in msgs) == 1


def test_an_image_a_seeing_model_saw_is_described_when_a_blind_one_replays_it(described):
    store = Store({'p1.jpg': (b'barn', 'image/jpeg'), 's1.jpg': (b'sheet', 'image/jpeg')})
    h = _history('<<IMG::tool:s1.jpg>>\n(image img:s1.jpg)\ncats')

    msgs = h.get_messages_for_llm(image_window=3, image_loader=store.get_tool_image,
                                  caption_loader=store.image_caption)
    assert d.caption_blind(msgs, BLIND, store=store) == 2

    assert msgs[0]['content'] == 'look\n\n(image img:p1.jpg)\n\nImage: barn in words.'
    replay = next(m for m in msgs if _REPLAY_NOTE in str(m['content']))
    assert replay['content'] == _REPLAY_NOTE + '\n\nImage: sheet in words.'
    assert store.captions == {'p1.jpg': 'Image: barn in words.', 's1.jpg': 'Image: sheet in words.'}
    assert len(described) == 2


# ── the lanes ────────────────────────────────────────────────────────────────

def test_tool_lane_keeps_the_description_on_the_row(sm, described, monkeypatch):
    from core.chat.chat_tool_calling import _extract_tool_images
    monkeypatch.setattr(sm, '_effective_chat_name', lambda: 'pics')     # the turn's own chat
    sm.add_user_message('take a picture')       # a live turn: the chat is never empty by now
    result = {'text': 'snap', 'images': [{'data': _b64(_png()), 'media_type': 'image/png'}]}

    text, llm_images = _extract_tool_images(result, sm, BLIND, 'webcam')

    image_id = re.search(r'\(image img:([^)]+)\)', text).group(1)
    assert llm_images == [] and 'Image: picture in words.' in text
    assert sm.image_caption(image_id) == 'Image: picture in words.'


def test_a_saved_event_image_is_described_through_its_row(monkeypatch):
    from core.continuity.execution_context import ExecutionContext
    seen = []

    def for_image(image_id, raw=None, *, private=None, store=None):
        seen.append((image_id, raw, private))
        return 'Image: barn in words.'
    monkeypatch.setattr(d, 'for_image', for_image)
    ctx = ExecutionContext.__new__(ExecutionContext)
    ctx.provider = BLIND

    llm, persist = ctx._build_user_message(
        'look\n<<IMG::tool:abc.png>>', [{'data': _b64(b'barn'), 'media_type': 'image/png', 'id': 'abc.png'}])

    assert seen == [('abc.png', b'barn', False)]
    assert llm == persist == 'look\n<<IMG::tool:abc.png>>\n\nImage: barn in words.'


def test_the_lanes_are_wired_to_the_image_row():
    def src(rel):
        return (ROOT / rel).read_text(encoding='utf-8')
    assert 'block["id"] = handle[4:]' in src('core/chat/chat.py')
    assert 'image_describe.caption_blind(messages, provider' in src('core/chat/chat_streaming.py')
    assert 'image_describe.for_image(stored_id, raw, store=history)' in src('core/chat/chat_tool_calling.py')
    assert 'img["id"] = img_id' in src('core/continuity/executor.py')
    assert 'image_describe.for_image(img["id"], raw, private=private)' in src('core/continuity/execution_context.py')


# ── one dropdown: any model line ─────────────────────────────────────────────

def test_picking_a_model_line_forces_vision_on_for_the_describe_call(monkeypatch):
    import core.chat.llm_providers as lp
    import core.chat.llm_providers.resolve as rz
    prov = MagicMock()
    prov.chat_completion.return_value = SimpleNamespace(content='A barn.')
    sel = SimpleNamespace(key='glm', provider=prov, model='', effective_model='glm-flash', display_name='GLM Flash')
    resolve = MagicMock(return_value=sel)
    monkeypatch.setattr(rz, 'resolve', resolve)
    monkeypatch.setattr(rz, 'providers_config',
                        lambda: {'glm': {'enabled': True, 'supports_images': False}, 'other': {'enabled': True}})
    monkeypatch.setattr(lp, 'get_generation_params', lambda *a, **k: {})
    monkeypatch.setattr(d, 'engine', lambda: 'glm')

    assert d.describe(_png(), private=False).endswith('A barn.')

    cfg = resolve.call_args.kwargs['cfg']
    assert cfg['glm']['supports_images'] is True and cfg['glm']['enabled'] is True
    assert 'supports_images' not in cfg['other']


def test_the_picker_lists_every_enabled_model_line():
    src = (ROOT / 'interfaces/web/static/views/settings.js').read_text(encoding='utf-8')
    assert "(d.providers || []).filter(p => p.enabled)\n" in src
    assert 'p.enabled && p.supports_images' not in src
