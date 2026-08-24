"""Tests for daemon state helpers and core compat shim."""

from plugins.discord.lib.core_compat import ensure_execution_context_images_support
from plugins.discord.runtime import daemon_state


def test_is_daemon_alive_reflects_shared_state():
    from plugins.discord import daemon as daemon_mod

    daemon_state.handle = None
    assert daemon_mod.is_daemon_alive() is False

    class FakeThread:
        def is_alive(self):
            return True

    class FakeHealth:
        state = 'ready'

    class FakeContainer:
        health = FakeHealth()

    handle = daemon_state.RuntimeHandle(
        plugin_name='discord',
        plugin_loader=object(),
        settings={},
    )
    handle.thread = FakeThread()
    handle.container = FakeContainer()
    daemon_state.handle = handle

    assert daemon_mod.is_daemon_alive() is True
    assert daemon_mod.get_health_state() == 'ready'

    daemon_state.handle = None


def test_legacy_get_client_and_clients_view():
    from plugins.discord import daemon as daemon_mod

    daemon_state.handle = None
    assert daemon_mod.get_client('bot') is None
    assert list(daemon_mod._clients) == []
    assert getattr(daemon_mod, '_loop') is None

    class FakeClient:
        guilds = []

    class FakeTransport:
        def __init__(self):
            self._accounts = {
                'bot': {'name': 'bot', 'state': 'connected', 'client': FakeClient()},
            }

        def get_client(self, account_name):
            return (self._accounts.get(account_name) or {}).get('client')

        def client_map(self):
            return {
                name: state.get('client')
                for name, state in self._accounts.items()
                if state.get('client') is not None
            }

        def list_connected(self):
            return ['bot']

    class FakeThread:
        def is_alive(self):
            return True

    handle = daemon_state.RuntimeHandle(
        plugin_name='discord',
        plugin_loader=object(),
        settings={},
    )
    handle.thread = FakeThread()
    handle.loop = object()
    handle.container = type('C', (), {'transport': FakeTransport(), 'health': type('H', (), {'state': 'ready'})()})()
    daemon_state.handle = handle

    assert daemon_mod.get_client('bot') is handle.container.transport._accounts['bot']['client']
    assert daemon_mod.list_connected() == ['bot']
    assert list(daemon_mod._clients.keys()) == ['bot']
    assert daemon_mod._clients['bot'] is daemon_mod.get_client('bot')
    assert getattr(daemon_mod, '_loop') is handle.loop

    daemon_mod._clients.pop('bot', None)
    assert daemon_mod.get_client('bot') is None

    daemon_state.handle = None


def test_execution_context_images_shim_is_idempotent():
    try:
        ensure_execution_context_images_support()
        from core.continuity.execution_context import ExecutionContext
    except ModuleNotFoundError:
        import pytest
        pytest.skip('core continuity stack not available in test env')

    first = ExecutionContext.run
    ensure_execution_context_images_support()
    assert ExecutionContext.run is first
    assert 'images' in __import__('inspect').signature(ExecutionContext.run).parameters
