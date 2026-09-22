"""Wave E (hunt 2026-09-12): DM policy, the image fetch cap,
fence-aware chunks."""

import time
from types import SimpleNamespace

from plugins.discord.conversation.reply_style_service import ReplyStyleService
from plugins.discord.models.settings import SettingsStore
from plugins.discord.conversation.images import FETCH_MAX_BYTES, fetch_bytes


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
    try:
        fetch_bytes('https://cdn.example/x.png')
    except ValueError as exc:
        assert 'over' in str(exc)
    else:
        raise AssertionError('a body past the cap must be refused')
    assert calls['stream'] is True and calls['allow_redirects'] is False
    assert FETCH_MAX_BYTES == 10 * 1024 * 1024


def test_chunker_closes_and_reopens_code_fences():
    svc = ReplyStyleService.__new__(ReplyStyleService)
    svc.message_limit = 60
    body = '```py\n' + '\n'.join(f'line {i} of code' for i in range(12)) + '\n```'
    chunks = svc._split_message('here is code:\n\n' + body)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.count('```') % 2 == 0, chunk
    assert chunks[1].startswith('```')
