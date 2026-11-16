"""What the Sapphire Launcher (its own repo, which cannot see this one) reads
from core, pinned here so a change fails HERE first (2026-11-15, the list the
launcher side wrote out: tmp/easy-mode-launcher-handoff.md, section 7).

A failure in this file is not a bug to fix by editing the test: it means the
launcher has to be told before the change ships. The other direction (what core
reads from the launcher: llm.json, llm-run.json, --llm-status) is pinned by the
launcher's own contract test.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TELL = "the Sapphire Launcher reads this: tell its repo before changing it"


def test_the_web_port_lives_under_server_in_the_settings():
    defaults = json.loads((ROOT / 'core' / 'settings_defaults.json').read_text(encoding='utf-8'))
    assert defaults['server']['WEB_UI_PORT'] == 8073, TELL


def test_the_instance_lock_is_where_and_what_the_launcher_expects(tmp_path, monkeypatch):
    import hashlib
    import os
    import core.setup
    from core import single_instance
    monkeypatch.setattr(core.setup, 'get_config_dir', lambda: tmp_path / 'config')
    path = Path(single_instance.lock_path(tmp_path))
    user = os.path.normcase(os.path.realpath(tmp_path / 'user'))
    assert path == tmp_path / 'config' / 'locks' / f"instance-{hashlib.sha256(user.encode('utf-8')).hexdigest()[:16]}.lock", TELL
    src = (ROOT / 'core' / 'single_instance.py').read_text(encoding='utf-8')
    # byte 0 is the lock's own (Windows locks it); the launcher reads from byte 1
    assert """fh.write(b'\\n' + f"pid={os.getpid()} since={time.strftime('%Y-%m-%d %H:%M:%S')}\\n".encode())""" in src, TELL
    assert "os.path.join(str(root), 'user', '.instance.lock')" in src, TELL       # where it lived before 2026-10-27
    assert (single_instance.EXIT_ANOTHER_INSTANCE, single_instance.EXIT_CONFIG) == (43, 44), TELL


def test_health_says_ok_and_the_log_keeps_its_name():
    assert 'out = {"status": "ok"}' in (ROOT / 'core' / 'routes' / 'chat.py').read_text(encoding='utf-8'), TELL
    assert "'user/logs/sapphire.log'" in (ROOT / 'core' / 'sapphire_logging.py').read_text(encoding='utf-8'), TELL


def test_a_graceful_stop_is_sigterm_or_ctrl_break():
    main = (ROOT / 'main.py').read_text(encoding='utf-8')
    assert "signal.signal(signal.SIGTERM, handle_signal)" in main and "hasattr(signal, 'SIGBREAK')" in main, TELL


def test_an_install_is_known_by_these_files_and_these_package_lists():
    for name in ('main.py', 'sapphire.py', 'core/setup.py', 'requirements.txt', 'install/requirements-stt.txt',
                 'install/requirements-tts.txt', 'install/requirements-wakeword.txt'):
        assert (ROOT / name).is_file(), f"{name}: {TELL}"


def test_the_password_and_the_keys_are_where_the_launchers_resets_look():
    import core.setup
    from core import credentials_manager
    assert core.setup.SECRET_KEY_FILE.name == 'secret_key', TELL                 # deleting it is "Reset Password"
    assert credentials_manager.CREDENTIALS_FILE.name == 'credentials.json', TELL
    assert core.setup.SECRET_KEY_FILE.parent == credentials_manager.CREDENTIALS_FILE.parent, TELL
    llm = credentials_manager.DEFAULT_CREDENTIALS['llm']                         # "Reset API Keys" blanks each api_key
    assert llm and all('api_key' in entry for entry in llm.values()), TELL
    assert 'services' in credentials_manager.DEFAULT_CREDENTIALS, TELL


def test_plugin_manifests_keep_the_fields_the_launcher_reads():
    manifests = list((ROOT / 'core' / 'plugins').glob('*/plugin.json'))
    assert manifests, TELL
    seen = set()
    for path in manifests:
        m = json.loads(path.read_text(encoding='utf-8'))
        assert m.get('name'), f"{path.parent.name}: {TELL}"
        seen |= set(m)
    assert {'default_enabled', 'pip_dependencies'} <= seen, TELL                 # by these names, where a plugin has them


def test_the_torch_rule_the_launcher_copied_has_not_moved():
    """Copied, not read: the launcher installs torch at deps time by this rule."""
    from core import gpu_install
    assert gpu_install.MIN_COMPUTE_CAP == 5.0 and gpu_install.TORCH_SPEC == 'torch>=2.7.0', TELL
    assert gpu_install.INDEXES == ('cu126', 'cu128'), TELL
    assert gpu_install.INDEX_URL == 'https://download.pytorch.org/whl/{index}', TELL
    assert [gpu_install.index_for(c) for c in (5.0, 7.4, 7.5, 12.0, None)] == ['cu126', 'cu126', 'cu128', 'cu128', 'cu128'], TELL
