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

    def read_messages(self, channel, count=20):
        self.calls.append(('read_messages', channel, count))
        return [{'message_id': 'm1', 'content': 'hello'}]

    def send_message_sync(self, channel, text, reply_to_message_id=None, account_name=None):
        self.calls.append(('send_message_sync', channel, text, reply_to_message_id, account_name))
        return {'status': 'sent', 'channel_id': channel}

    def upload_file_sync(self, channel, file_path, caption='', account_name=None):
        self.calls.append(('upload_file_sync', channel, file_path, caption, account_name))
        return {'status': 'uploaded', 'channel_id': channel}

    def send_gif_sync(self, channel, query, account_name=None):
        self.calls.append(('send_gif_sync', channel, query, account_name))
        return {'status': 'sent'}

    def add_reaction_sync(self, channel, message_id, emoji, account_name=None):
        self.calls.append(('add_reaction_sync', channel, message_id, emoji, account_name))
        return {'status': 'reacted', 'emoji': emoji}


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


def test_execute_routes_through_transport(monkeypatch, tmp_path):
    runtime = FakeRuntime()
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_channel_id.set('c1')
    tools._reply_message_id.set('1521787194761678918')

    test_file = tmp_path / 'note.txt'
    test_file.write_text('hello', encoding='utf-8')

    msg, ok = tools.execute('discord_get_servers', {})
    assert ok is True
    assert 'Guild' in msg

    msg, ok = tools.execute('discord_read_messages', {'channel': 'c1', 'count': 5})
    assert ok is True

    msg, ok = tools.execute('discord_send_message', {'channel': 'c1', 'text': 'hello'})
    assert ok is True
    assert runtime.reply_style_service.marked == [('1521787194761678918', 'hello')]

    msg, ok = tools.execute('discord_upload_file', {'file_path': str(test_file), 'channel': 'c1'})
    assert ok is True

    msg, ok = tools.execute('discord_send_gif', {'query': 'https://example.com/a.gif', 'channel': 'c1'})
    assert ok is True

    msg, ok = tools.execute('discord_add_reaction', {'emoji': '🔥', 'channel': 'c1', 'message_id': '1521787194761678918'})
    assert ok is True


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


def test_task_followup_message_id_not_used_as_reply_target(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_channel_id.set('c1')
    tools._reply_message_id.set(None)

    class FakeEventData:
        @staticmethod
        def get():
            return {
                'channel_id': 'c1',
                'message_id': 'task-followup-1',
                'account': 'alpha',
                'task_follow_up': 'true',
            }

    monkeypatch.setattr('core.continuity.executor.current_event_data', FakeEventData())

    msg, ok = tools.execute('discord_send_message', {'channel': 'c1', 'text': 'drink water'})
    assert ok is True
    assert runtime.transport.calls[-1][3] is None
    assert tools._reply_message_id.get() is None
    assert runtime.reply_style_service.marked == [('task-followup-1', 'drink water')]


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


def test_duplicate_task_follow_up_send_is_suppressed(monkeypatch):
    runtime = FakeRuntime()
    runtime.world_model_service = type('W', (), {
        'task_repository': type('R', (), {
            'get_task': lambda self, task_id: {'id': task_id, 'status': 'completed'},
        })(),
    })()
    monkeypatch.setattr(tools, 'get_runtime', lambda: runtime)
    tools._reply_channel_id.set('c1')

    class FakeEventData:
        @staticmethod
        def get():
            return {
                'channel_id': 'c1',
                'message_id': 'task-followup-1',
                'account': 'alpha',
                'task_follow_up': 'true',
                'task_id': '1',
            }

    monkeypatch.setattr('core.continuity.executor.current_event_data', FakeEventData())

    msg, ok = tools.execute('discord_send_message', {'channel': 'c1', 'text': 'again'})
    assert ok is True
    assert 'already delivered' in msg.lower()
    assert runtime.transport.calls == []
