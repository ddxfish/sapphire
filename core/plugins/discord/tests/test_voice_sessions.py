"""S6: live voice sessions live in memory."""
from plugins.discord.voice.voice_sessions import VoiceSessions


def test_sessions_are_per_account_channel_and_close_cleanly():
    sessions = VoiceSessions()
    a = sessions.start('alpha', 'g1', 'c1')
    assert sessions.start('alpha', 'g1', 'c1') is a                     # idempotent
    b = sessions.start('alpha', '', 'c2')
    assert sessions.get('alpha', 'c1') is a and sessions.get_by_id(b.session_id) is b
    assert sessions.get_by_guild_channel('g1', 'c1') is a and sessions.get_by_guild_channel('', 'c2') is b
    assert [s.channel_id for s in sessions.list_active('alpha')] == ['c1', 'c2'] and sessions.list_active('beta') == []
    assert sessions.set_health(a.session_id, 'conversational').health == 'conversational'
    assert sessions.close(a.session_id) is a and sessions.get('alpha', 'c1') is None
    assert sessions.close('nope') is None
