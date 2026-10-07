"""Surface 5 P1 — claude-code plugin-worker validation + run_claude error handling.

PluginWorker runs Claude Code to build a Sapphire plugin, then validates the
output structure before declaring success. Weak validation = broken plugins
ship to disk and fail at load.

Covers:
  5.35 validate_plugin rejects ghost empty-capabilities manifests (Chaos #4)
  5.36 validate_plugin auto-fixes manifest atomically (tmp + os.replace)
  5.37 validate_plugin reports missing files with specific names (Chaos #10)
  5.38 run_claude treats nonzero exit as error even when stdout is parseable JSON (Chaos #6)
  5.39 run_claude handles unparseable stdout (falls through to line scan / error)
  5.40 plugin_worker fails when validation fails despite claude success

See tmp/coverage-test-plan.md Surface 5 P1.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _load_cct():
    if 'claude_code_tools_test' in sys.modules:
        return sys.modules['claude_code_tools_test']
    project_root = Path(__file__).resolve().parent.parent
    module_path = project_root / 'plugins' / 'claude-code' / 'tools' / 'claude_code_tools.py'
    spec = importlib.util.spec_from_file_location('claude_code_tools_test', module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['claude_code_tools_test'] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cct():
    return _load_cct()


# ─── 5.35 validate_plugin rejects ghost empty-capabilities plugin ────────────

def test_validate_plugin_rejects_empty_capabilities_ghost(cct, tmp_path):
    """[REGRESSION_GUARD] A manifest with {name, description} but NO capabilities
    is a ghost plugin — it loads without error but does nothing. Before the
    Chaos #4 fix, the per-capability loops vacuously passed and such
    plugins shipped as 'valid'.
    """
    # Build a ghost plugin in tmp_path
    (tmp_path / 'plugin.json').write_text(json.dumps({
        'name': 'ghost_plugin',
        'version': '1.0.0',
        'description': 'does nothing — ghost',
        # No capabilities key at all
    }))
    result = cct._validate_plugin(str(tmp_path))
    assert result.get('manifest_valid') is True
    assert result.get('has_capability') is False, \
        "ghost plugin with no capabilities must flag has_capability=False"


def test_validate_plugin_accepts_tools_capability(cct, tmp_path):
    """Inverse: a manifest declaring tools IS a real plugin (provided files exist)."""
    (tmp_path / 'plugin.json').write_text(json.dumps({
        'name': 'real_plugin', 'version': '1.0.0', 'description': 'has tools',
        'capabilities': {'tools': ['tools/main.py']},
    }))
    (tmp_path / 'tools').mkdir()
    (tmp_path / 'tools' / 'main.py').write_text('def x(): pass\n')
    result = cct._validate_plugin(str(tmp_path))
    assert result.get('has_capability') is True


@pytest.mark.parametrize("cap_key,cap_value", [
    ('hooks', [{'event': 'pre_chat', 'fn': 'h.hook'}]),
    ('daemon', {'entry': 'daemon.py'}),
    ('routes', ['routes/r.py']),
    ('providers', {'tts': {'entry': 'p.py'}}),
    ('scopes', [{'key': 'x', 'setting': 'x_scope'}]),
    ('settings', [{'key': 'A', 'type': 'string'}]),
    ('schedule', {'tasks': [{'id': 't', 'cron': '* * * * *'}]}),
])
def test_validate_plugin_accepts_any_real_capability(cct, tmp_path, cap_key, cap_value):
    """Any non-empty capability (hooks/daemon/routes/providers/scopes/settings/schedule)
    flags has_capability=True."""
    (tmp_path / 'plugin.json').write_text(json.dumps({
        'name': 'thing', 'version': '1.0.0', 'description': 't',
        'capabilities': {cap_key: cap_value},
    }))
    result = cct._validate_plugin(str(tmp_path))
    assert result.get('has_capability') is True, \
        f"capability {cap_key}={cap_value!r} not accepted"


# ─── 5.36 Manifest auto-fix uses atomic tmp+replace ──────────────────────────

def test_validate_plugin_auto_fixes_manifest_atomically(cct, tmp_path):
    """[DATA_INTEGRITY] When Claude Code writes tools as a string instead of
    a list, the validator auto-normalizes it. This write MUST be atomic —
    tmp + os.replace — so a plugin file-watcher reading concurrently never
    sees a half-written manifest (would corrupt load on restart).
    """
    (tmp_path / 'plugin.json').write_text(json.dumps({
        'name': 'autofix', 'version': '1.0.0', 'description': 'test',
        'capabilities': {'tools': 'tools/main.py'},  # STRING, not list
    }))
    (tmp_path / 'tools').mkdir()
    (tmp_path / 'tools' / 'main.py').write_text('def x(): pass\n')

    # Spy on os.replace to confirm atomic rename was used
    orig_replace = os.replace
    calls = []
    def _spy(src, dst):
        calls.append((str(src), str(dst)))
        return orig_replace(src, dst)

    with patch.object(os, 'replace', _spy):
        result = cct._validate_plugin(str(tmp_path))

    assert result.get('manifest_auto_fixed') is True
    # Atomic rename fired: src is a .tmp file, dst is the real plugin.json
    assert any(src.endswith('.tmp') and dst.endswith('plugin.json')
               for src, dst in calls), \
        f"atomic rename not used — calls: {calls}"

    # Manifest now has list (on-disk)
    data = json.loads((tmp_path / 'plugin.json').read_text())
    assert data['capabilities']['tools'] == ['tools/main.py']


# ─── 5.37 validate_plugin reports specific missing file names ────────────────

def test_validate_plugin_reports_missing_files_with_specific_names(cct, tmp_path):
    """[REGRESSION_GUARD] When a declared tool file is missing, the validator
    lists the specific path(s) in `_missing_files` so a follow-up agent or
    human doesn't have to hunt for which file the manifest declared that's
    absent on disk (Chaos #10)."""
    (tmp_path / 'plugin.json').write_text(json.dumps({
        'name': 'missing_files', 'version': '1.0.0', 'description': 't',
        'capabilities': {
            'tools': ['tools/one.py', 'tools/two.py'],
            'providers': {'tts': {'entry': 'tts_provider.py'}},
        },
    }))
    # Only write one of the declared files
    (tmp_path / 'tools').mkdir()
    (tmp_path / 'tools' / 'one.py').write_text('')
    # two.py and tts_provider.py deliberately missing

    result = cct._validate_plugin(str(tmp_path))
    assert result.get('files_exist') is False
    missing = result.get('_missing_files', [])
    assert 'tools/two.py' in missing, f"missing file not named: {missing}"
    assert any('tts_provider.py' in m for m in missing), \
        f"provider file absence not reported: {missing}"


# ─── 5.38 run_claude: nonzero exit is an error even with stdout JSON ─────────


# ─── the kind reports validation after a plugin-mode turn ─────────────────────────────

def test_plugin_mode_report_names_failed_checks_never_a_green_success(cct, tmp_path):
    """A plugin that fails validation is reported as failing, with the missing
    files named and the next step (say the failures back to the agent) - never
    a success-shaped report."""
    import importlib
    k = importlib.import_module('plugins.claude-code.agent_kind')
    ws = tmp_path / 'user' / 'plugins' / 'broken'
    ws.mkdir(parents=True)
    (ws / 'plugin.json').write_text('{"name": "broken", "capabilities": {"tools": ["tools/x.py"]}}')

    class _E:
        def install_carrier(self, a): return None
        def release_carrier(self, t): pass
        def finished(self, a): pass
        def report_out(self, a, t): pass
        def event_out(self, a, k): pass
        def question_out(self, a, q): pass
    a = k.Agent({'id': 'x', 'name': 'Forge', 'kind': 'claude_code', 'chat': 'c', 'mission': 'm',
                 'options': {'mode': 'plugin', 'name': 'broken'}, 'privacy': False}, _E())
    a.workspace = str(ws)
    text = a._plugin_check()
    assert '✗ files_exist' in text and 'tools/x.py' in text
    assert 'activate_plugin' not in text and "agent_action('Forge', 'say'" in text
    (ws / 'tools').mkdir()
    (ws / 'tools' / 'x.py').write_text('x = 1\n')
    text = a._plugin_check()
    assert '✗' not in text and "activate_plugin('broken')" in text

