from datetime import datetime
from unittest.mock import MagicMock

from plugins.discord.models.settings import SettingsStore
from plugins.discord.proactive.test_paths import collect_proactive_diagnostics, run_proactive_test


def _settings(**kwargs):
    store = SettingsStore()
    store.global_overlay.proactive.update(kwargs)
    return store


def test_diagnostics_reports_wrong_greeting_hour(tmp_path):
    runtime = MagicMock()
    runtime.greeting_service.evaluate.return_value = []
    runtime.outreach_service.evaluate.return_value = []
    runtime.outreach_service._greeting_blocked_hours.return_value = set()
    runtime.outreach_service._in_sleep_hours.return_value = False
    runtime.sleep_service.evaluate_goodnight.return_value = []
    runtime.sleep_service.in_sleep_hours.return_value = False
    runtime.sleep_service.GOODNIGHT_MINUTES = (0, 15, 30, 45)
    runtime.transport.list_connected.return_value = ['alpha']
    runtime.proactive_repository.get_sleep_state.return_value = {}
    runtime.settings_store = _settings(
        greeting_enabled=True,
        greeting_utc_hour=9,
        greeting_targets=['alpha:c1'],
    )
    runtime.channel_repository.load_settings_store.return_value = runtime.settings_store

    diag = collect_proactive_diagnostics(runtime)
    assert diag['greeting']['would_fire_now'] is False
    assert any('hour' in hint.lower() for hint in diag['greeting']['hints'])


def test_run_proactive_test_greeting_dry_run(tmp_path):
    runtime = MagicMock()
    runtime.transport.list_connected.return_value = ['alpha']
    runtime.proactive_executor._resolve_message_text.return_value = 'Good morning!'
    runtime.settings_store = _settings(
        greeting_enabled=True,
        greeting_targets=['alpha:c1'],
    )
    runtime.channel_repository.load_settings_store.return_value = runtime.settings_store
    runtime.sleep_service = MagicMock()

    result = run_proactive_test(runtime, 'greeting', dry_run=True)

    assert result['sent'] == 0
    assert result['results'][0]['status'] == 'dry_run'
    assert result['results'][0]['preview'] == 'Good morning!'
    runtime.proactive_executor.execute.assert_not_called()


def test_run_proactive_test_no_targets():
    runtime = MagicMock()
    runtime.transport.list_connected.return_value = ['alpha']
    runtime.settings_store = _settings(greeting_enabled=True, greeting_targets=[])
    runtime.channel_repository.load_settings_store.return_value = runtime.settings_store
    runtime.greeting_service.evaluate.return_value = []
    runtime.outreach_service.evaluate.return_value = []
    runtime.outreach_service._greeting_blocked_hours.return_value = set()
    runtime.outreach_service._in_sleep_hours.return_value = False
    runtime.sleep_service.evaluate_goodnight.return_value = []
    runtime.sleep_service.in_sleep_hours.return_value = False
    runtime.sleep_service.GOODNIGHT_MINUTES = (0, 15, 30, 45)
    runtime.proactive_repository.get_sleep_state.return_value = {}

    result = run_proactive_test(runtime, 'greeting')

    assert result['error'] == 'no_matching_targets'
