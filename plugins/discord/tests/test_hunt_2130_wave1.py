"""Discord hunt 2.13.0 — wave 1 (LHF + Krem-wants) regression pins.

Row numbers refer to tmp/zeebie-discord/hunt-2130-results.md. Every test here
pins a behaviour that used to be silently wrong; none touch the network.
"""
from __future__ import annotations

import inspect
import logging
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]


# ── row 16: think-tag stripper eats prose ──────────────────────────────────
def test_think_tag_in_prose_survives():
    from plugins.discord.conversation.think_tags import strip_think_tags
    text = 'You can wrap reasoning in <think> tags like this. Models do it a lot.'
    assert strip_think_tags(text) == text


def test_think_block_still_stripped():
    from plugins.discord.conversation.think_tags import strip_think_tags
    assert strip_think_tags('<think>secret</think>Hello there') == 'Hello there'
    assert strip_think_tags('<think>cut off mid-thought') == ''
    assert strip_think_tags('leftover thinking</think>Answer.') == 'Answer.'


def test_backticked_think_tag_is_prose():
    from plugins.discord.conversation.think_tags import strip_think_tags
    text = 'Reasoning models emit `<think>` and `</think>` around their thoughts.'
    assert strip_think_tags(text) == text


# ── row 34: fence rebalancer flips every later chunk ───────────────────────
def test_prose_backticks_do_not_carry_a_fence():
    from plugins.discord.conversation.reply_style_service import ReplyStyleService
    chunks = ['To fence code you type ``` before it.', 'Then your code.', "That's it!"]
    assert ReplyStyleService._rebalance_fences(chunks) == chunks


def test_real_fence_cut_by_a_chunk_is_closed_and_reopened():
    from plugins.discord.conversation.reply_style_service import ReplyStyleService
    out = ReplyStyleService._rebalance_fences(['```py\nprint(1)', 'print(2)\n```'])
    assert out[0].endswith('\n```')
    assert out[1].startswith('```\n')


# ── row 74: one clock for hour comparisons ─────────────────────────────────
def test_user_hour_follows_config_timezone(monkeypatch):
    import config
    from plugins.discord.lib.server_time import user_hour
    now = datetime(2026, 6, 30, 12, 0)                    # naive = OS-local
    monkeypatch.setattr(config, 'USER_TIMEZONE', 'UTC', raising=False)
    from zoneinfo import ZoneInfo
    assert user_hour(now) == now.astimezone(ZoneInfo('UTC')).hour
    monkeypatch.setattr(config, 'USER_TIMEZONE', '', raising=False)
    assert user_hour(now) == now.hour


# ── row 9: reach fails closed ─────────────────────────────────────────────
def test_reach_error_fails_closed_without_guild_id():
    from plugins.discord.tools import discord_tools as dt
    transport = SimpleNamespace(channel_reach_sync=lambda target, account_name=None: {'guild_id': 'g2', 'is_dm': False})
    with patch.object(dt, '_event_data', return_value={'channel_id': '1', 'guild_id': ''}), \
         patch.object(dt, '_tools_stay_in_server', return_value=True), \
         patch.object(dt, '_transport', return_value=transport):
        assert dt._reach_error('1', 'bot') is None                 # the event channel itself
        err = dt._reach_error('2', 'bot')
        assert err and "can't tell which server" in err


# ── row 27: an unknown account never becomes "the first bot" ───────────────
def test_state_for_account_refuses_unknown_name():
    from plugins.discord.transport.discord_execution import DiscordExecution
    transport = SimpleNamespace(_accounts={'real': {'client': object()}}, list_connected=lambda: ['real'])
    ex = DiscordExecution(transport)
    with pytest.raises(RuntimeError):
        ex._state_for_account('ghost')
    assert ex._state_for_account('real')[0] == 'real'


# ── row 43: a typo'd target is loud ────────────────────────────────────────
def test_parse_target_warns_on_bad_entry(caplog):
    from plugins.discord.conversation.ignored_channels import parse_target
    with caplog.at_level(logging.WARNING):
        assert parse_target('justachannelid') is None
    assert 'not account:channel_id' in caplog.text


# ── row 11: a replaced turn cannot touch the new turn's stream ─────────────
def test_source_stale_generation_gates_feed_finish_wait():
    from plugins.discord.voice.discord_conversation_source import DiscordConversationSource
    src = DiscordConversationSource.__new__(DiscordConversationSource)
    src.driver = SimpleNamespace(_turn_gen=5)
    assert src._stale() is False                     # never started: not gated
    src._gen = 5
    assert src._stale() is False
    src.driver._turn_gen = 6
    assert src._stale() is True
    src.feed_chunk({'audio_b64': 'x'})               # returns before touching playback
    src.playback_service = MagicMock()
    src.wait()
    src.playback_service.wait.assert_not_called()


# ── row 14: the image-IN lane is wired ─────────────────────────────────────
def test_conversation_service_accepts_the_image_lane_and_container_passes_it():
    from plugins.discord.conversation.conversation_service import ConversationService
    assert 'image_lane' in inspect.signature(ConversationService.__init__).parameters
    src = (ROOT / 'plugins/discord/runtime/container.py').read_text(encoding='utf-8')
    assert 'image_lane=self.image_lane,' in src


# ── row 24: the listen-only lane does not leak ─────────────────────────────
def test_discard_pending_drops_payload_and_latches():
    from plugins.discord.conversation.conversation_service import ConversationService
    svc = ConversationService.__new__(ConversationService)
    svc._pending = {'m1': {'payload': {}, 'at': 0}}
    svc.reply_style_service = MagicMock()
    svc.discard_pending('m1')
    assert 'm1' not in svc._pending
    svc.reply_style_service.discard.assert_called_once_with('m1')


def test_sweep_pending_drops_stale_rows():
    import time
    from plugins.discord.conversation.conversation_service import ConversationService
    svc = ConversationService.__new__(ConversationService)
    svc._pending = {'old': {'at': time.time() - 4000}, 'new': {'at': time.time()}}
    assert svc._sweep_pending() == 1
    assert list(svc._pending) == ['new']


# ── row 15: prompt context keyed to the trigger, not observations[-1] ──────
def test_prompt_context_uses_trigger_identity():
    from plugins.discord.conversation.prompt_context_service import PromptContextService
    repo = MagicMock()
    repo.get_recent_messages.return_value = []
    svc = PromptContextService(message_repository=repo)
    def obs(author, mid):
        return SimpleNamespace(account_name='bot', channel_id='c1', channel_name='general', guild_name='G',
                               guild_id='g1', author_id=author, attachments=[], message_id=mid,
                               clean_content='hi', is_dm=False)
    alice, bob = obs('alice', 'm1'), obs('bob', 'm2')
    ctx = svc.build(SimpleNamespace(observations=[alice, bob]), trigger=alice)
    assert ctx['author_id'] == 'alice'


# ── row 10: source tripwire for the one-liner ──────────────────────────────
def test_source_tripwires():
    listener = (ROOT / 'plugins/discord/voice/voice_listener_service.py').read_text(encoding='utf-8')
    assert 'interrupt_active_turn(session.session_id)\n            else:' not in listener       # row 10
