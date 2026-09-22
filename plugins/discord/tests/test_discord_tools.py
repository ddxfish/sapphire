import asyncio
import sys
import types
from contextvars import ContextVar

from plugins.discord.tools import discord_tools as tools


class FakeTransport:
    def __init__(self):
        self.calls = []
        self._connected = ['alpha']
        self._accounts = {'alpha': {}, 'leona_bot_test': {}}

    def list_connected(self):
        return list(self._connected)

    def list_servers(self):
        self.calls.append(('list_servers',))
        return [{'id': 'g1', 'name': 'Guild'}]

    def read_messages(self, channel, count=20, account_name=None):
        self.calls.append(('read_messages', channel, count, account_name))
        return [{'message_id': 'm1', 'author': 'alice', 'content': 'hello', 'created_at': '2026-09-13T13:00', 'attachments': 0}]

    def send_message_sync(self, channel, text, reply_to_message_id=None, account_name=None):
        self.calls.append(('send_message_sync', channel, text, reply_to_message_id, account_name))
        return {'status': 'sent', 'channel_id': channel}

    def send_gif_sync(self, channel, query, account_name=None):
        self.calls.append(('send_gif_sync', channel, query, account_name))
        return {'status': 'sent'}

    def add_reaction_sync(self, channel, message_id, emoji, account_name=None):
        self.calls.append(('add_reaction_sync', channel, message_id, emoji, account_name))
        return {'status': 'reacted', 'emoji': emoji}

    def send_file_sync(self, channel, data, filename, caption='', account_name=None):
        self.calls.append(('send_file_sync', channel, len(data), filename, caption, account_name))
        return {'status': 'sent', 'message_id': '900', 'channel_id': channel}

    def list_channels_sync(self, kind='text'):
        rows = [
            {'account': 'alpha', 'guild_id': 'g1', 'guild_name': 'Guild', 'channel_id': 'c1', 'channel_name': 'general'},
            {'account': 'alpha', 'guild_id': 'g2', 'guild_name': 'Other', 'channel_id': 'c9', 'channel_name': 'lobby'},
        ]
        if kind == 'voice':
            rows = [{'account': 'alpha', 'guild_id': 'g1', 'guild_name': 'Guild', 'channel_id': 'v1', 'channel_name': 'Voice Lounge'}]
        return rows

    def channel_reach_sync(self, channel, account_name=None):
        return {'c1': {'guild_id': 'g1', 'is_dm': False}, 'c9': {'guild_id': 'g2', 'is_dm': False},
                'dm1': {'guild_id': '', 'is_dm': True}}.get(str(channel), {'guild_id': '', 'is_dm': False})


class FakeReplyStyle:
    def __init__(self):
        self.marked = []
        self.gif_sent = set()
        from plugins.discord.conversation.reply_style_service import ReplyStyleService
        self._parser = ReplyStyleService()

    def parse_llm_output(self, text, strip_thinking=True):
        return self._parser.parse_llm_output(text, strip_thinking=strip_thinking)

    def mark_tool_sent(self, message_id, text=''):
        self.marked.append((message_id, text))

    def mark_gif_sent(self, message_id):
        self.gif_sent.add(message_id)

    def gif_already_sent(self, message_id):
        return message_id in self.gif_sent

    def mark_gif_sent(self, message_id):
        self.gif_sent.add(message_id)


class FakeGifService:
    def gif_allowed(self, settings):
        return True

    def maybe_send_gif(self, parsed_reply, *, account_name='', channel_id='', settings=None):
        return getattr(parsed_reply, 'gif_query', '') or None

    def search_gif_url(self, query, *, settings=None):
        return f'https://example.com/{query.replace(" ", "-")}.gif'

    def mark_sent(self, account_name, channel_id):
        return None


class FakeRuntime:
    def __init__(self):
        self.transport = FakeTransport()
        self.reply_style_service = FakeReplyStyle()
        self.gif_service = None


def test_execute_routes_through_transport(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_channel_id.set('c1')
    tools._reply_message_id.set('1521787194761678918')

    msg, ok = tools.execute('discord_get_servers', {})
    assert ok is True
    assert 'Guild' in msg

    msg, ok = tools.execute('discord_read_messages', {'channel': 'c1', 'count': 5})
    assert ok is True
    assert '[m1]' in msg
    assert 'alice: hello' in msg

    msg, ok = tools.execute('discord_send_message', {'channel': 'c1', 'text': 'hello'})
    assert ok is True
    assert runtime.reply_style_service.marked == [('1521787194761678918', 'hello')]

    msg, ok = tools.execute('discord_send_gif', {'query': 'https://example.com/a.gif', 'channel': 'c1'})
    assert ok is True

    msg, ok = tools.execute('discord_add_reaction', {'emoji': '🔥', 'channel': 'c1', 'message_id': '1521787194761678918'})
    assert ok is True


def test_upload_file_tool_is_gone():
    # Removed 2026-09-13 (hunt H3): any absolute path + exists() = one persuaded
    # turn posts the owner's secrets into a channel. Must not come back quietly.
    assert 'discord_upload_file' not in tools.AVAILABLE_FUNCTIONS
    assert not hasattr(tools, 'discord_upload_file')
    msg, ok = tools.execute('discord_upload_file', {'file_path': '/etc/hostname', 'channel': 'c1'})
    assert ok is False
    assert 'unknown function' in msg.lower()


def test_resolve_channel_id_maps_account_name_to_reply_channel(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_channel_id.set('135957860654776321')
    tools._reply_account.set('leona_bot_test')

    assert tools._resolve_channel_id('leona_bot_test') == '135957860654776321'
    assert tools._resolve_channel_id('135957860654776321') == '135957860654776321'
    assert tools._default_account() == 'leona_bot_test'


def test_discord_send_message_strips_gif_tag_and_posts_gif(monkeypatch):
    runtime = FakeRuntime()
    runtime.gif_service = FakeGifService()
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_channel_id.set('c1')
    tools._reply_message_id.set('1521787194761678918')
    tools._reply_account.set('alpha')

    msg, ok = tools.discord_send_message(
        channel='c1',
        text='hello there\n[gif:cat reaching up]',
    )

    assert ok is True
    assert runtime.transport.calls[0][:3] == ('send_message_sync', 'c1', 'hello there')
    assert runtime.transport.calls[1][0] == 'send_gif_sync'
    assert 'cat-reaching-up' in runtime.transport.calls[1][2]
    assert runtime.reply_style_service.marked == [('1521787194761678918', 'hello there')]
    assert '1521787194761678918' in runtime.reply_style_service.gif_sent


def test_discord_send_gif_skips_when_already_sent(monkeypatch):
    runtime = FakeRuntime()
    runtime.reply_style_service.mark_gif_sent('m1')
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_message_id.set('m1')

    msg, ok = tools.discord_send_gif(query='https://example.com/a.gif', channel='c1')

    assert ok is True
    assert 'already sent' in msg.lower()
    assert runtime.transport.calls == []


def test_discord_send_gif_with_account_name_uses_reply_channel(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_channel_id.set('135957860654776321')
    tools._reply_account.set('leona_bot_test')

    msg, ok = tools.execute('discord_send_gif', {
        'query': 'https://example.com/a.gif',
        'channel': 'leona_bot_test',
    })

    assert ok is True
    assert runtime.transport.calls[-1] == (
        'send_gif_sync',
        '135957860654776321',
        'https://example.com/a.gif',
        'leona_bot_test',
    )


def test_default_account_from_scope_discord_when_event_has_no_account(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_account.set(None)
    tools._reply_channel_id.set('c1')
    tools._reply_message_id.set('1521787194761678918')

    scope_var = ContextVar('scope_discord', default='default')
    scope_var.set('leona_bot_test')
    fm_mod = types.ModuleType('core.chat.function_manager')
    fm_mod.scope_discord = scope_var
    for pkg in ('core', 'core.chat'):
        if pkg not in sys.modules:
            monkeypatch.setitem(sys.modules, pkg, types.ModuleType(pkg.split('.')[-1]))
    monkeypatch.setitem(sys.modules, 'core.chat.function_manager', fm_mod)

    class FakeEventData:
        @staticmethod
        def get():
            return {
                'channel_id': 'c1',
                'message_id': '1521787194761678918',
            }

    monkeypatch.setattr('core.continuity.executor.current_event_data', FakeEventData())

    assert tools._default_account() == 'leona_bot_test'

    msg, ok = tools.execute('discord_send_message', {'channel': 'c1', 'text': 'hello'})
    assert ok is True
    assert tools._reply_account.get() == 'leona_bot_test'
    call = runtime.transport.calls[-1]
    assert call[0] == 'send_message_sync'
    assert call[1] == 'c1'
    assert call[2] == 'hello'
    assert call[4] == 'leona_bot_test'


class FakeVoiceService:
    def __init__(self, join_status='joined'):
        self.join_status = join_status
        self.joins = []
        self.leaves = []

    def join(self, intention):
        self.joins.append(intention)
        if self.join_status == 'joined':
            return {'status': 'joined', 'session': {}}
        if self.join_status == 'blocked':
            return {'status': 'blocked', 'reason': 'voice_disabled'}
        return {'status': 'error', 'reason': 'voice_connect_failed'}

    def leave(self, intention):
        self.leaves.append(intention)
        return {'status': 'left'}


class FakeVoiceTransport:
    def __init__(self, connections=None):
        self._rows = connections or []

    def list_connections(self, account_name=None):
        return list(self._rows)


def _voice_runtime(join_status='joined', connections=None):
    runtime = FakeRuntime()
    runtime.voice_service = FakeVoiceService(join_status)
    runtime.voice_transport = FakeVoiceTransport(connections)
    runtime.transport.resolve_voice_channel_sync = lambda ref, account_name=None: {
        'channel_id': '555', 'guild_id': 'g1', 'channel_name': 'Voice Chat 1',
    }
    return runtime


def test_discord_join_voice_joins_resolved_channel(monkeypatch):
    runtime = _voice_runtime()
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_account.set('alpha')

    msg, ok = tools.execute('discord_join_voice', {'channel': 'voice chat 1'})

    assert ok is True
    assert 'Voice Chat 1' in msg
    intention = runtime.voice_service.joins[0]
    assert intention.account_name == 'alpha'
    assert intention.channel_id == '555'
    assert intention.guild_id == 'g1'
    assert intention.reason == 'tool_request'


def test_discord_join_voice_blocked_points_at_settings(monkeypatch):
    runtime = _voice_runtime(join_status='blocked')
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_account.set('alpha')

    msg, ok = tools.execute('discord_join_voice', {'channel': 'voice chat 1'})

    assert ok is False
    assert 'disabled' in msg.lower()


def test_discord_join_voice_unresolved_channel_errors(monkeypatch):
    runtime = _voice_runtime()

    def _boom(ref, account_name=None):
        raise RuntimeError(f"Voice channel '{ref}' not found")

    runtime.transport.resolve_voice_channel_sync = _boom
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_account.set('alpha')

    msg, ok = tools.execute('discord_join_voice', {'channel': 'nope'})

    assert ok is False
    assert 'not found' in msg.lower()
    assert runtime.voice_service.joins == []


def test_discord_leave_voice_without_channel_leaves_all(monkeypatch):
    runtime = _voice_runtime(connections=[
        {'channel_id': '555', 'guild_id': 'g1'},
        {'channel_id': '777', 'guild_id': 'g2'},
    ])
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_account.set('alpha')

    msg, ok = tools.execute('discord_leave_voice', {})

    assert ok is True
    assert [i.channel_id for i in runtime.voice_service.leaves] == ['555', '777']


def test_discord_leave_voice_not_connected(monkeypatch):
    runtime = _voice_runtime(connections=[])
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_account.set('alpha')

    msg, ok = tools.execute('discord_leave_voice', {})

    assert ok is True
    assert 'not connected' in msg.lower()
    assert runtime.voice_service.leaves == []


# ── Wave F: tools revision (D11) ─────────────────────────────────────────

def test_every_tool_is_flagged_and_every_parameter_described():
    for tool in tools.TOOLS:
        name = tool['function']['name']
        assert tool.get('is_local') in (True, False, 'endpoint'), name
        assert 'network' in tool, name
        for pname, spec in tool['function']['parameters']['properties'].items():
            assert spec.get('description'), f'{name}.{pname} has no description'


def test_list_channels_names_and_ids(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    msg, ok = tools.execute('discord_list_channels', {})
    assert ok and '#general (c1) — Guild (g1)' in msg and '#lobby (c9)' in msg
    msg, ok = tools.execute('discord_list_channels', {'server': 'other'})
    assert ok and '#lobby' in msg and '#general' not in msg
    msg, ok = tools.execute('discord_list_channels', {'kind': 'all'})
    assert ok and '#Voice Lounge (v1)' in msg and '[voice]' in msg


def test_send_receipts_carry_message_ids(monkeypatch):
    runtime = FakeRuntime()
    runtime.transport.send_message_sync = lambda channel, text, reply_to_message_id=None, account_name=None: {
        'status': 'sent', 'channel_id': channel, 'messages': [{'message_id': '777', 'channel_id': channel}]}
    runtime.transport.send_gif_sync = lambda channel, query, account_name=None: {'status': 'sent', 'message_id': '778'}
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_channel_id.set('c1')
    msg, ok = tools.execute('discord_send_message', {'channel': 'c1', 'text': 'hi'})
    assert ok and 'message_id 777' in msg
    msg, ok = tools.execute('discord_send_gif', {'query': 'https://x/a.gif', 'channel': 'c1'})
    assert ok and 'message_id 778' in msg


def test_send_image_posts_resolved_bytes_never_a_path(monkeypatch, tmp_path):
    pytest = __import__('pytest')
    Image = pytest.importorskip('PIL.Image')
    from core import images as ci

    runtime = FakeRuntime()
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_channel_id.set('c1')
    png = tmp_path / 'diagram.png'
    Image.new('RGB', (8, 8), 'red').save(png)
    monkeypatch.setattr(ci, 'resolve', lambda source, **kw: ci._file(png) if source == 'doc:7' else (_ for _ in ()).throw(ci.ImageError('no')))
    result, ok = tools.execute('discord_send_image', {'source': 'doc:7', 'channel': 'c1', 'caption': 'how she works'})
    assert ok is True
    assert isinstance(result, dict) and result['images'] and 'message_id 900' in result['text']
    call = next(c for c in runtime.transport.calls if c[0] == 'send_file_sync')
    assert call[1] == 'c1' and call[2] > 0 and call[3].endswith('.png') and call[4] == 'how she works'
    # a filesystem path is refused before anything is resolved
    msg, ok = tools.execute('discord_send_image', {'source': '/etc/hostname', 'channel': 'c1'})
    assert ok is False and 'disk' in msg


def test_tools_stay_in_the_server_inside_an_event(monkeypatch):
    from core.continuity.executor import current_event_data

    runtime = FakeRuntime()
    runtime.settings_store = None
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    token = current_event_data.set({'channel_id': 'c1', 'guild_id': 'g1', 'message_id': '1521787194761678918', 'account': 'alpha'})
    try:
        msg, ok = tools.execute('discord_send_message', {'channel': 'c1', 'text': 'here'})
        assert ok is True                                  # same channel
        msg, ok = tools.execute('discord_send_message', {'channel': 'c9', 'text': 'elsewhere'})
        assert ok is False and 'different server' in msg   # another server
        msg, ok = tools.execute('discord_send_message', {'channel': 'dm1', 'text': 'psst'})
        assert ok is False and 'DMs' in msg                # a DM target
        msg, ok = tools.execute('discord_read_messages', {'channel': 'c9'})
        assert ok is False
        msg, ok = tools.execute('discord_list_channels', {})
        assert ok and '#general' in msg and '#lobby' not in msg   # roster narrowed to this server
    finally:
        current_event_data.reset(token)
    # operator chat (no event): full reach
    msg, ok = tools.execute('discord_send_message', {'channel': 'c9', 'text': 'from home'})
    assert ok is True
