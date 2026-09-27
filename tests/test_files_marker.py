"""The FILES marker (core/attachments.py): files a tool or a plugin's own turn
hands the user. UI-only, so every copy the model reads must drop it, and its
urls must never leave this app's own plugin routes. 2026-09-26.
"""
import json
from pathlib import Path

import pytest

from core import attachments
from core.chat.chat_tool_calling import strip_ui_markers
from core.chat.history import _UI_MARKER_RE

ROOT = Path(__file__).resolve().parent.parent
MP3 = '/api/plugin/fm1/song/4a6d591cf2.mp3'
MID = '/api/plugin/fm1/song/4a6d591cf2.mid'


def _marker(title='Copper Light'):
    return attachments.marker(title, [{'url': MP3, 'name': 'copper-light.mp3'},
                                      {'url': MID, 'name': 'copper-light.mid'}])


def test_marker_shape():
    m = _marker()
    assert m.startswith('<!--FILES:{') and m.endswith('}-->') and '\n' not in m
    body = json.loads(m[len('<!--FILES:'):-len('-->')])
    assert body == {'title': 'Copper Light', 'items': [{'url': MP3, 'name': 'copper-light.mp3'},
                                                       {'url': MID, 'name': 'copper-light.mid'}]}


def test_marker_name_falls_back_and_is_cleaned():
    body = json.loads(attachments.marker('a\nb', [{'url': MP3 + '?dl=1'},
                                                  {'url': MID, 'name': ' we"ird<>\r\n.mid '}])[10:-3])
    assert body['title'] == 'a b'
    assert [i['name'] for i in body['items']] == ['4a6d591cf2.mp3', 'weird.mid']


def test_no_items_no_marker():
    assert attachments.marker('t', []) == '' and attachments.marker('t', None) == ''


def test_items_are_capped():
    m = attachments.marker('t', [{'url': MP3}] * 40)
    assert len(json.loads(m[10:-3])['items']) == attachments.MAX_ITEMS


@pytest.mark.parametrize('bad', [
    'https://evil.example/x.mp3', '//evil.example/x.mp3', 'javascript:alert(1)',
    '/api/plugin/fm1/../../settings', '/api/settings', '/api/plugin/fm1//x.mp3',
    '/api/plugin/fm1/x.mp3?a=<b>', 'data:audio/mp3;base64,AAAA', '', None,
])
def test_only_plugin_routes(bad):
    assert not attachments.safe_url(bad)
    with pytest.raises(ValueError):
        attachments.marker('t', [{'url': bad, 'name': 'x'}])


def test_stripped_from_every_copy_the_model_reads():
    body = 'Saved Copper Light.\n' + _marker('a}-->b')
    assert attachments.strip(body) == 'Saved Copper Light.'
    assert strip_ui_markers(body) == 'Saved Copper Light.'                  # live tool cycle
    assert _UI_MARKER_RE.sub('', body).strip() == 'Saved Copper Light.'     # history replay
    assert '<!--FILES:' in strip_ui_markers(body, keep_img=True)            # history-less lanes keep the row
    both = '<<IMG::tool:a.jpg>>\n<!--GALLERY:["u"]-->\n' + _marker() + '\ntext'
    assert strip_ui_markers(both) == 'text'


def test_strip_leaves_other_text_alone():
    for text in ('', None, 'plain', '<!-- a comment -->', '<!--FILES: not a marker -->'):
        assert attachments.strip(text) == text


def test_history_replay_checks_for_the_marker():
    """The replay strip is gated on a cheap `in` check; FILES must be in it."""
    src = (ROOT / 'core' / 'chat' / 'history.py').read_text(encoding='utf-8')
    assert "'<!--FILES:' in c" in src


def test_live_turn_wire_copy_drops_the_marker():
    """A plugin's own turn carries the marker in the user text: history keeps
    it, this turn's wire copy must not."""
    src = (ROOT / 'core' / 'chat' / 'chat.py').read_text(encoding='utf-8')
    build = src[src.index('def _build_base_messages'):]
    build = build[:build.index('\n    def ', 10)]
    assert 'attachments.strip(user_input)' in build
    assert build.index('attachments.strip(user_input)') < build.index('count_tokens(user_input)')


def test_one_files_renderer():
    """The FILES marker is parsed in exactly one place in the frontend."""
    static = ROOT / 'interfaces' / 'web' / 'static'
    parsers = [p.relative_to(ROOT).as_posix() for p in static.rglob('*.js')
               if '<!--FILES' in p.read_text(encoding='utf-8', errors='replace')]
    assert parsers == ['interfaces/web/static/shared/files-marker.js'], parsers


def test_live_turn_wire_copy_is_built_without_the_marker(monkeypatch):
    """The same guarantee, run for real: build this turn's wire messages from a
    user text that carries the row, and nothing the model reads may hold it."""
    from types import SimpleNamespace
    from core.chat import chat as chat_mod

    class _Session:
        def get_messages_for_llm(self, reserved_tokens=0):
            return [{'role': 'assistant', 'content': 'earlier turn'}]

        def get_chat_settings(self):
            return {}

    monkeypatch.setattr('core.ghost_messages.build_ghost_message', lambda *a, **k: '')
    me = SimpleNamespace(session_manager=_Session(), system=None,
                         _get_system_prompt=lambda: ('SYSTEM', 'user', ''),
                         _get_rag_context=lambda text: None)
    take = '[Keys: 3 notes]\nC4 E4 G4\n(The take is saved.)\n' + _marker('Keys take')
    msgs = chat_mod.LLMChat._build_base_messages(me, take)
    assert msgs[-1] == {'role': 'user', 'content': '[Keys: 3 notes]\nC4 E4 G4\n(The take is saved.)'}
    assert not any('<!--FILES:' in str(m.get('content')) for m in msgs)
    plain = chat_mod.LLMChat._build_base_messages(me, 'just typing <!-- a comment -->')
    assert plain[-1]['content'] == 'just typing <!-- a comment -->'        # ordinary text untouched
