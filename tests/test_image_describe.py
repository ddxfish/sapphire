"""core.image_describe — the ONE describer for images a text-only model was
meant to see (2026-09-26): CLIP by default, a vision provider by setting,
CLIP again on every failure; both lanes call it."""
import base64
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from core import image_describe as d

ROOT = Path(__file__).resolve().parents[1]


def _png(color=(200, 30, 30)) -> bytes:
    out = BytesIO(); Image.new('RGB', (8, 8), color).save(out, format='PNG'); return out.getvalue()


def _gif() -> bytes:
    frames = [Image.new('RGB', (8, 8), c) for c in ((255, 0, 0), (0, 255, 0))]
    out = BytesIO(); frames[0].save(out, format='GIF', save_all=True, append_images=frames[1:], duration=50, loop=0)
    return out.getvalue()


@pytest.fixture
def lane(monkeypatch):
    """Engine pinned to a provider, CLIP stubbed, resolver + params faked."""
    import core.chat.llm_providers as lp
    import core.chat.llm_providers.resolve as rz
    monkeypatch.setattr(d, '_clip', lambda b: 'CLIP-VIBE')
    monkeypatch.setattr(lp, 'get_generation_params', lambda *a, **k: {'temperature': 0.2})
    prov = MagicMock()
    prov.chat_completion.return_value = SimpleNamespace(content='A red square on a white wall.')
    sel = SimpleNamespace(key='visionbox', provider=prov, model='', effective_model='vlm-1', display_name='Vision Box')
    resolve = MagicMock(return_value=sel)
    monkeypatch.setattr(rz, 'resolve', resolve)
    monkeypatch.setattr(d, 'engine', lambda: 'visionbox')
    return SimpleNamespace(prov=prov, resolve=resolve)


def test_clip_is_the_default_and_never_touches_the_resolver(monkeypatch):
    import core.chat.llm_providers.resolve as rz
    resolve = MagicMock(); monkeypatch.setattr(rz, 'resolve', resolve)
    monkeypatch.setattr(d, '_clip', lambda b: 'CLIP-VIBE')
    monkeypatch.setattr(d, 'engine', lambda: 'clip')
    assert d.describe(_png()) == 'CLIP-VIBE'
    assert not resolve.called
    assert json.loads((ROOT / 'core' / 'settings_defaults.json').read_text(encoding='utf-8'))['images']['IMAGE_DESCRIBE_ENGINE'] == 'clip'


def test_provider_engine_captions_the_image_literally(lane):
    text = d.describe(_png(), private=False)
    assert text == 'Image (described by Vision Box; the replying model could not see it): A red square on a white wall.'
    kw = lane.resolve.call_args.kwargs
    assert kw['require_images'] is True and kw['private'] is False and kw['prompt_name'] is None and kw['health'] == 'skip'
    assert lane.resolve.call_args.args[0] == 'visionbox'
    messages, tools, params = lane.prov.chat_completion.call_args.args
    assert tools is None and params['max_tokens'] == d.MAX_TOKENS and params['temperature'] == 0.2
    content = messages[0]['content']
    assert messages[0]['role'] == 'user' and content[0] == {'type': 'text', 'text': d.PROMPT}
    assert content[1]['type'] == 'image' and content[1]['media_type'] == 'image/png'
    assert base64.b64decode(content[1]['data']) == _png()


def test_privacy_rides_from_the_turn_when_the_caller_does_not_say(lane):
    from core.chat.function_manager import scope_private
    token = scope_private.set(True)
    try:
        d.describe(_png())
    finally:
        scope_private.reset(token)
    assert lane.resolve.call_args.kwargs['private'] is True
    d.describe(_png(), private=False)
    assert lane.resolve.call_args.kwargs['private'] is False


@pytest.mark.parametrize('break_it', ['refused', 'exploded', 'empty', 'not_configured'])
def test_every_provider_failure_falls_back_to_clip(lane, break_it):
    import core.chat.llm_providers.resolve as rz
    if break_it == 'refused':
        lane.resolve.side_effect = rz.ProviderUnavailable('blind')
    elif break_it == 'exploded':
        lane.prov.chat_completion.side_effect = RuntimeError('boom')
    elif break_it == 'empty':
        lane.prov.chat_completion.return_value = SimpleNamespace(content='   ')
    else:
        lane.resolve.side_effect = rz.PrivacyRefused('not local')
    assert d.describe(_png(), private=True) == 'CLIP-VIBE'


def test_gif_first_frame_becomes_png_and_stills_pass_through():
    data, media = d.prepare(_gif())
    assert media == 'image/png'
    im = Image.open(BytesIO(data))
    assert im.format == 'PNG' and im.convert('RGB').getpixel((0, 0)) == (255, 0, 0)   # frame 0, not frame 1
    png = _png()
    assert d.prepare(png) == (png, 'image/png')


def test_setting_is_registered_hot_with_help():
    from core.settings_manager import settings
    assert settings.validate_tier('IMAGE_DESCRIBE_ENGINE') == 'hot'
    help_ = json.loads((ROOT / 'core' / 'settings_help.json').read_text(encoding='utf-8'))
    assert 'CLIP' in help_['IMAGE_DESCRIBE_ENGINE']['long']
    tab = (ROOT / 'interfaces/web/static/views/settings-tabs/images.js').read_text(encoding='utf-8')
    assert "'IMAGE_DESCRIBE_ENGINE'" in tab


def test_provider_listing_carries_the_vision_verdict(monkeypatch):
    from core.chat.llm_providers import provider_registry as reg
    cfg = {'seer': {'enabled': True, 'display_name': 'Seer'}, 'blind': {'enabled': True}, 'off': {'enabled': False}}
    seer = MagicMock(); type(seer).supports_images = property(lambda self: True)
    blind = MagicMock(); type(blind).supports_images = property(lambda self: False)
    built = {'seer': seer, 'blind': blind}
    monkeypatch.setattr(reg, 'get_provider_by_key', lambda key, pc=None, *a, **k: built.get(key) if pc[key].get('enabled') else None)
    monkeypatch.setattr(reg, 'get_api_key', lambda *a, **k: 'k')
    rows = {p['key']: p['supports_images'] for p in reg.get_all_providers(cfg)}
    assert rows == {'seer': True, 'blind': False, 'off': False}


def test_both_lanes_call_the_describer_not_clip_directly():
    for rel in ('core/chat/chat_tool_calling.py', 'core/continuity/execution_context.py'):
        src = (ROOT / rel).read_text(encoding='utf-8')
        assert 'image_describe.describe(' in src, rel
        assert '_vibes.describe(' not in src, rel
