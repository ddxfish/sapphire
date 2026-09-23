"""The reply's context: transcript lines keyed to the trigger, and the per-channel cooldown."""
import time
from types import SimpleNamespace

from plugins.discord.conversation.context import ReplyCooldown, build_context, format_message_line, format_recent_history


def test_format_message_line_uses_the_best_name_and_caps_length():
    assert format_message_line({'author_name': 'Alice', 'content': 'hi\nthere'}) == 'Alice: hi there'
    assert format_message_line({'username': 'bob', 'content': ''}) == 'bob:'
    assert format_message_line({'content': 'x'}) == 'Unknown: x'
    long = format_message_line({'author_name': 'A', 'content': 'y' * 2000}, line_max_chars=20)
    assert len(long) == 20 and long.endswith('…')


def test_recent_history_excludes_the_trigger_itself():
    rows = [{'message_id': 'm1', 'author_name': 'A', 'content': 'one'}, {'message_id': 'm2', 'author_name': 'B', 'content': 'two'}]
    assert format_recent_history(rows, exclude_message_id='m2') == ['A: one']


def test_build_context_is_keyed_to_the_trigger_identity():
    """hunt 2.13.0 row 15: the gates key off the newest ADDRESSED message, so the transcript does too."""
    rows = [{'message_id': 'm1', 'author': 'alice', 'content': 'hi'}, {'message_id': 'm2', 'author': 'bob', 'content': 'yo'}]
    alice = SimpleNamespace(account_name='bot', channel_id='c1', channel_name='general', guild_name='G', guild_id='g1',
                            author_id='alice', attachments=[], message_id='m1')
    ctx = build_context(rows, alice)
    assert ctx['author_id'] == 'alice' and ctx['recent_history'] == ['bob: yo']
    assert build_context([], alice)['recent_history'] == []


def test_cooldown_per_channel_and_missing_author():
    cd = ReplyCooldown()
    settings = SimpleNamespace(safety=SimpleNamespace(rate_limit_seconds=3600))
    a = SimpleNamespace(account_name='bot', channel_id='c1', author_id='u1')
    assert cd.evaluate(a, settings)['allowed'] is True
    assert cd.evaluate(a, settings) == {'allowed': False, 'reason': 'cooldown'}
    b = SimpleNamespace(account_name='bot', channel_id='c2', author_id='u1')
    assert cd.evaluate(b, settings)['allowed'] is True                       # another channel, its own clock
    assert cd.evaluate(SimpleNamespace(account_name='bot', channel_id='c3', author_id=''), settings)['reason'] == 'missing_author'
    cd._last_reply_at[('bot', 'c1')] = time.time() - 4000
    assert cd.evaluate(a, settings)['allowed'] is True
