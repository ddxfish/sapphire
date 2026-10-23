"""The daemon entry: the shared handle (one module for both import paths), health, and the
reply handler's listen-only lane."""
from types import SimpleNamespace

from plugins.discord import daemon
from plugins.discord.runtime import daemon_state


def _fake_handle(container=None, alive=True):
    handle = daemon_state.RuntimeHandle(plugin_name='discord', plugin_loader=object(), settings={})
    handle.thread = SimpleNamespace(is_alive=lambda: alive)
    handle.container = container
    return handle


def test_the_handle_lives_in_the_shared_state_module_not_in_daemon_globals():
    # Core loads daemon.py under its own module name; a handle in daemon.py's
    # globals left every route saying "stopped" while the daemon ran (S7).
    assert not hasattr(daemon, 'handle')
    assert daemon.daemon_state is daemon_state


def test_is_daemon_alive_and_health_reflect_the_shared_handle(monkeypatch):
    monkeypatch.setattr(daemon_state, 'handle', None)
    assert daemon.is_daemon_alive() is False and daemon.get_health_state() == 'stopped'
    monkeypatch.setattr(daemon_state, 'handle', _fake_handle(container=None))
    assert daemon.get_health_state() == 'starting'
    container = SimpleNamespace(health=SimpleNamespace(state='ready'),
                                transport=SimpleNamespace(list_connected=lambda: ['sapphire'], get_client=lambda n: f'client:{n}'))
    monkeypatch.setattr(daemon_state, 'handle', _fake_handle(container))
    assert daemon.is_daemon_alive() is True and daemon.get_health_state() == 'ready'
    assert daemon.list_connected() == ['sapphire'] and daemon.get_client('sapphire') == 'client:sapphire'


def test_reply_handler_listen_only_clears_the_payload_and_delivers_nothing(monkeypatch):
    cleared, discarded, delivered = [], [], []
    container = SimpleNamespace(
        event_bridge=SimpleNamespace(clear_pending_payload=lambda mid: cleared.append(mid)),
        conversation_service=SimpleNamespace(discard_pending=lambda mid: discarded.append(mid),
                                             handle_llm_response=lambda t, e, r: delivered.append(r) or {'status': 'sent', 'chunks': 1}),
    )
    monkeypatch.setattr(daemon_state, 'handle', _fake_handle(container))
    out = daemon._reply_handler({'trigger_config': {'auto_reply': 'false'}}, {'message_id': 'm1'}, 'hello')
    assert out == {'status': 'skipped', 'reason': 'auto_reply_disabled'}
    assert cleared == ['m1'] and discarded == ['m1'] and delivered == []
    out = daemon._reply_handler({'trigger_config': {}}, {'message_id': 'm2'}, 'hello')
    assert out['status'] == 'sent' and delivered == ['hello'] and cleared == ['m1', 'm2']
    monkeypatch.setattr(daemon_state, 'handle', None)
    assert daemon._reply_handler({}, {'message_id': 'm3'}, 'x') is None
