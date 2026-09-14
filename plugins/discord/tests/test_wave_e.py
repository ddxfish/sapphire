"""Wave E (hunt 2026-09-12): local-only side lanes, DM policy, vision caps, typo
guards, fence-aware chunks."""

import time
from types import SimpleNamespace

from plugins.discord.conversation.delivery_style_service import DeliveryStyleService
from plugins.discord.conversation.reply_style_service import ReplyStyleService
from plugins.discord.models.settings import SettingsStore
from plugins.discord.vision.vision_bridge import CAPTION_MAX_CHARS, FETCH_MAX_BYTES, VisionBridge


def test_side_lane_auto_mode_asks_core_for_local_providers_only(monkeypatch):
    from plugins.discord.sapphire import llm_settings as ls

    seen = {}

    def fake_first(providers_config, fallback_order, timeout, force_privacy=False, **kw):
        seen['force_privacy'] = force_privacy
        return None

    import core.chat.llm_providers as providers
    monkeypatch.setattr(providers, 'get_first_available_provider', fake_first)
    monkeypatch.setattr(ls, '_providers_config', lambda: {})
    system = SimpleNamespace(llm_chat=object())
    assert ls.resolve_discord_llm_provider(system, 'auto', local_only=True) == (None, None, None)
    assert seen['force_privacy'] is True
    ls.resolve_discord_llm_provider(system, 'auto', local_only=False)
    assert seen['force_privacy'] is False


def test_side_lanes_default_to_local_only_when_settings_unreadable():
    from plugins.discord.sapphire.llm_settings import side_lanes_local_only

    assert side_lanes_local_only() is True   # no booted plugin system here → fail closed


def test_dms_default_off_and_budget_counts_per_person_per_day():
    from plugins.discord.conversation.conversation_service import ConversationService

    s = SettingsStore().resolve()
    assert s.safety.allow_direct_messages is False
    assert s.safety.dm_daily_budget == 30

    svc = ConversationService.__new__(ConversationService)
    settings = SimpleNamespace(safety=SimpleNamespace(dm_daily_budget=2))
    alice = SimpleNamespace(account_name='bot', author_id='alice')
    bob = SimpleNamespace(account_name='bot', author_id='bob')
    assert svc._dm_within_budget(alice, settings) and svc._dm_within_budget(alice, settings)
    assert svc._dm_within_budget(alice, settings) is False      # third today
    assert svc._dm_within_budget(bob, settings) is True         # someone else, own budget
    assert svc._dm_within_budget(alice, SimpleNamespace(safety=SimpleNamespace(dm_daily_budget=0))) is True


def test_caption_normalizer_strips_think_and_caps():
    bridge = VisionBridge()
    out = bridge._normalize({'summary': '<think>secret reasoning</think>' + 'x' * 2000, 'ocr_text': 'y' * 2000,
                             'entities': list(range(40)), 'tone': 't' * 500}, source='vision')
    assert 'secret' not in out['summary'] and len(out['summary']) == CAPTION_MAX_CHARS
    assert len(out['ocr_text']) == CAPTION_MAX_CHARS and len(out['entities']) == 12 and len(out['tone']) == 80


def test_fetch_streams_with_a_cap_and_never_follows_redirects(monkeypatch):
    from core import net

    calls = {}

    class Resp:
        status_code = 200
        headers = {'Content-Type': 'image/png'}

        def iter_content(self, n):
            for _ in range(200):
                yield b'\x89PNG\r\n\x1a\n' + b'\x00' * (n - 8)

    def fake_get(url, **kw):
        calls.update(kw)
        return Resp()

    monkeypatch.setattr(net, 'get', fake_get)
    bridge = VisionBridge()
    try:
        bridge._fetch_bytes('https://cdn.example/x.png')
    except ValueError as exc:
        assert 'over' in str(exc)
    else:
        raise AssertionError('a body past the cap must be refused')
    assert calls['stream'] is True and calls['allow_redirects'] is False
    assert FETCH_MAX_BYTES == 10 * 1024 * 1024


def test_non_image_attachments_are_never_fetched_for_vision():
    from plugins.discord.conversation.media_service import MediaService
    from plugins.discord.models.media import MediaArtifact

    class Bridge:
        def describe_media(self, *a, **k):
            raise AssertionError('must not fetch')

    svc = MediaService(vision_bridge=Bridge())
    art = MediaArtifact(message_id='m', channel_id='c', account_name='bot', media_kind='attachment',
                        source_url='https://cdn/x.zip', filename='x.zip')
    out = svc.interpret_artifact(art, image_understanding_enabled=True)
    assert out['source'] == 'metadata'


def test_typo_simulator_leaves_links_code_emoji_and_mentions_alone():
    svc = DeliveryStyleService.__new__(DeliveryStyleService)
    text = 'definitely see https://example.com/definitely and `definitely` and :definitely: and <@definitely>'
    for _ in range(40):
        pair = svc._introduce_common_typo(text)
        if pair is None:
            continue
        typo, _corrected = pair
        assert 'https://example.com/definitely' in typo
        assert '`definitely`' in typo and ':definitely:' in typo and '<@definitely>' in typo
    for _ in range(40):
        out = svc._introduce_subtle_typo('look at https://example.com/reallylongpath and `codeword` :thumbsup: <#123456>')
        assert 'https://example.com/reallylongpath' in out and '`codeword`' in out
        assert ':thumbsup:' in out and '<#123456>' in out


def test_chunker_closes_and_reopens_code_fences():
    svc = ReplyStyleService.__new__(ReplyStyleService)
    svc.message_limit = 60
    body = '```py\n' + '\n'.join(f'line {i} of code' for i in range(12)) + '\n```'
    chunks = svc._split_message('here is code:\n\n' + body)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.count('```') % 2 == 0, chunk
    assert chunks[1].startswith('```')
