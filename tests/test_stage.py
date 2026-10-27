"""The stage: one app beside the chat (tmp/stage-plan.md, 2026-10-25).

functions/stage.py (open_stage) + the STAGE marker, /api/apps `places`, and
source tripwires for the frontend rules the privacy scout set: a private chat
has no stage, the stage never takes the chat's composer, and it holds focus.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).absolute().parent.parent
STATIC = ROOT / 'interfaces' / 'web' / 'static'


def _stage_tool():
    spec = importlib.util.spec_from_file_location('stage_tool', ROOT / 'functions' / 'stage.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def tool(monkeypatch):
    mod = _stage_tool()
    monkeypatch.setattr(mod, 'stage_apps', lambda: {'browser': {'label': 'Browser', 'icon': '\U0001F310'}})
    monkeypatch.setattr(mod, '_private_turn', lambda: False)
    monkeypatch.setattr('core.ui_markers.no_surface', lambda: '')
    return mod


def test_open_stage_puts_a_card_marker_in_her_result(tool):
    out, ok = tool.execute('open_stage', {'app': 'Browser'})
    assert ok and out.startswith('Opened Browser beside the chat')
    m = re.search(r'<!--STAGE:(\{.*?\})-->', out)
    assert m and json.loads(m.group(1)) == {'app': 'browser', 'label': 'Browser', 'icon': '\U0001F310'}


def test_the_marker_never_reaches_the_model(tool):
    from core.ui_markers import UI_MARKER_RE
    out, _ = tool.execute('open_stage', {'app': 'browser'})
    assert 'STAGE' not in UI_MARKER_RE.sub('', out)


def test_no_app_lists_the_menu_and_a_wrong_one_is_refused(tool):
    assert tool.execute('open_stage', {}) == ('Apps that can open beside this chat: browser.', True)
    out, ok = tool.execute('open_stage', {'app': 'doom'})
    assert not ok and 'Available: browser' in out


def test_a_private_chat_has_no_stage(tool, monkeypatch):
    monkeypatch.setattr(tool, '_private_turn', lambda: True)
    out, ok = tool.execute('open_stage', {'app': 'browser'})
    assert not ok and 'private chats have no stage' in out and 'STAGE' not in out


def test_no_chat_page_no_card(tool, monkeypatch):
    monkeypatch.setattr('core.ui_markers.no_surface', lambda: 'this turn came in over MCP')
    out, ok = tool.execute('open_stage', {'app': 'browser'})
    assert not ok and 'MCP' in out and 'STAGE' not in out


def test_unknown_privacy_counts_as_private(monkeypatch):
    mod = _stage_tool()
    import core.chat.function_manager as fm

    class Broken:
        def get(self):
            raise RuntimeError('no context')
    monkeypatch.setattr(fm, 'scope_private', Broken())
    assert mod._private_turn() is True


@pytest.mark.parametrize('declared,places', [
    (None, ['apps']),
    (['apps', 'stage'], ['apps', 'stage']),
    (['stage', 'elsewhere'], ['stage']),
    (['elsewhere'], ['apps']),
    ('stage', ['apps']),
])
def test_app_places_are_cleaned(declared, places):
    import core.api_fastapi  # noqa: F401 - the routes package loads after the app, as at boot
    from core.routes.plugins import _app_places
    assert _app_places({} if declared is None else {'places': declared}) == places


# --- frontend contracts (source tripwires) ---------------------------------------

def _src(rel):
    return (STATIC / rel).read_text(encoding='utf-8')


def test_organs_take_the_chat_columns_own_composer():
    assert "'#view-chat .chat-body > .chat-main > .form-wrapper'" in _src('surface/organs.js')


def test_the_stage_is_a_focus_owning_sibling_before_the_chat_column():
    s = _src('surface/surface.js')
    assert '<section class="chat-stage" hidden data-keeps-focus>' in s
    assert s.index("${mode.stage ? STAGE : ''}") < s.index('<div class="chat-main">')
    assert 'class="sb-resizer"' not in s.split('const STAGE')[1].split('`;')[0]   # its own gutter


def test_a_private_or_unknown_chat_tears_the_stage_down():
    s = _src('surface/stage.js')
    assert "private: !settings || !!settings.private_chat" in s
    assert "const show = visible && !!wanted && !!chat.name && !chat.private;" in s
    assert 'sessionStorage' in s and 'updateChatSettings' not in s and 'publish' not in s


def test_the_chat_view_runs_the_stage_only_while_shown():
    s = _src('views/chat.js')
    assert 'async show() {\n        setStageVisible(true);' in s
    assert 'hide() {\n        setStageVisible(false);' in s


def test_stage_cards_render_from_history_and_the_asking_tab_opens_live():
    assert _src('ui-parsing.js').count('buildStageCards(stageCards)') == 2
    assert "new CustomEvent('sapphire:stage_open'" in _src('api.js')
    assert "addEventListener('sapphire:stage_open'" in _src('shared/stage-marker.js')


def test_the_live_tool_end_carries_stage_payloads_only():
    from core.ui_markers import stage_cards
    text = 'Opened.\n<!--STAGE:{"app":"browser","label":"Browser","icon":"x"}-->\n<!--ASK:{"id":"a"}-->'
    assert stage_cards(text) == [{'app': 'browser', 'label': 'Browser', 'icon': 'x'}]
    assert stage_cards('<!--STAGE:{torn-->') == [] and stage_cards(None) == []
    src = (ROOT / 'core' / 'chat' / 'chat_streaming.py').read_text(encoding='utf-8')
    assert 'stage = stage_cards(result_str)' in src and '"result": clean_result[:500]' in src


# --- the 2026-10-27 hunt ---------------------------------------------------------

def test_only_open_stage_makes_a_card():
    s = _src('shared/stage-marker.js')
    assert "if (tool !== 'open_stage') return '';" in s
    assert 'parseStageMarker(askStripped, toolName)' in _src('ui-parsing.js')
    assert 'parseStageMarker(askStripped, toolName)' in _src('ui-streaming.js')
    src = (ROOT / 'core' / 'chat' / 'chat_streaming.py').read_text(encoding='utf-8')
    assert "stage_cards(result_str) if function_name == 'open_stage' else None" in src


def test_a_paste_into_an_app_stays_in_the_app():
    assert "closest?.('[data-keeps-focus], .app-host-content')" in _src('handlers/send-handlers.js')


def test_the_dragged_width_is_a_variable_the_phone_stack_ignores():
    assert 'flex: 0 1 var(--stage-w, 58%)' in _src('style.css')
    s = _src('surface/stage.js')
    assert 'flexBasis' not in s and "setProperty('--stage-w'" in s


def test_the_stage_closes_before_a_private_transcript_paints():
    cm = _src('features/chat-manager.js')
    assert cm.index("'chat-switching'") < cm.index('const len = await refresh(false);')
    assert "addEventListener('chat-switching'" in _src('surface/stage.js')


def test_open_stage_saves_nothing_for_a_private_chat_and_ctx_chat_stays_current():
    s = _src('surface/stage.js')
    body = s.split('export async function openStage')[1].split('export function closeStage')[0]
    assert body.index('if (chat.private)') < body.index('sessionStorage.setItem')
    assert 'get chat() {' in s
