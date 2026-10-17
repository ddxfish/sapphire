"""H11 / M23 (hunt 2026-09-12): patches report, never raise; DAVE gate is version-based."""

import sys
from pathlib import Path
from types import SimpleNamespace

from plugins.discord.voice import dave_voice_patches as dvp
from plugins.discord.voice import patch_registry as pr

PLUGIN = Path(__file__).resolve().parents[1]


def test_apply_patch_records_every_outcome_and_never_raises():
    pr.PATCH_STATUS.clear()

    def moved():
        raise ImportError('No module named discord.voice.receive.reader')

    def renamed():
        raise AttributeError("type object 'PacketRouter' has no attribute 'run'")

    def broke():
        raise RuntimeError('boom')

    assert pr.apply_patch('moved', moved) is False
    assert pr.apply_patch('renamed', renamed) is False
    assert pr.apply_patch('broke', broke) is False
    assert pr.apply_patch('fine', lambda: None) is True
    assert pr.PATCH_STATUS['moved'].startswith('missing module')
    assert pr.PATCH_STATUS['renamed'].startswith('renamed target')
    assert pr.PATCH_STATUS['broke'].startswith('failed')
    assert pr.PATCH_STATUS['fine'] == 'applied'
    assert sorted(pr.missing_patches()) == ['broke', 'moved', 'renamed']


def test_voice_stack_info_carries_patch_outcomes():
    from plugins.discord.voice.voice_deps import voice_stack_info

    pr.PATCH_STATUS.clear()
    pr.apply_patch('x', lambda: (_ for _ in ()).throw(ImportError('gone')))
    info = voice_stack_info()
    assert info['patches']['x'].startswith('missing module')
    assert info['patches_missing'] == ['x']


def test_dave_gate_is_version_based_not_source_sniffing():
    assert dvp._upstream_dave_decrypt_fixed('2.8.0rc2.dev22+g6e71bfffb') is True   # the pin
    assert dvp._upstream_dave_decrypt_fixed('2.9.1') is True                        # anything released after
    assert dvp._upstream_dave_decrypt_fixed('2.6.1') is False                       # predates the fix
    assert dvp._upstream_dave_decrypt_fixed('') is False
    dave_src = (PLUGIN / 'voice' / 'dave_voice_patches.py').read_text(encoding='utf-8')
    assert 'import inspect' not in dave_src and 'getsource(' not in dave_src


def test_apply_dave_voice_patches_never_raises_when_pycord_moved(monkeypatch):
    # The reader module vanishes → the whole apply used to raise out of
    # lifecycle.start and take text chat down with it.
    monkeypatch.setattr(dvp, '_DAVE_PATCHED', False)
    monkeypatch.setitem(sys.modules, 'davey', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'discord.voice.receive.reader', None)   # ImportError on import
    dvp.apply_dave_voice_patches()
    assert dvp.patch_mode() == 'none'


def test_apply_pycord_patches_never_raises_when_opus_is_missing(monkeypatch):
    from plugins.discord.voice import pycord_patches as pp

    pr.PATCH_STATUS.clear()
    monkeypatch.setattr(pp, '_APPLIED', False)
    for mod in ('discord.opus', 'discord.voice.client', 'discord.voice.receive.router', 'discord.voice.gateway'):
        monkeypatch.setitem(sys.modules, mod, None)
    pp.apply_pycord_voice_patches()
    assert pr.PATCH_STATUS['opus_decoder_resilience'].startswith('missing module')
    assert pr.PATCH_STATUS['aead_log_flood_limiter'] == 'applied'   # needs no py-cord module


def test_dead_code_stays_dead():
    src = (PLUGIN / 'voice' / 'dave_voice_patches.py').read_text(encoding='utf-8')
    assert 'Deprecated' not in src and 'apply_dave_supplement_patches' not in src
    assert '_ClientsView' not in (PLUGIN / 'daemon.py').read_text(encoding='utf-8')
    assert not (PLUGIN / 'transport' / 'discord_commands.py').exists()
    assert not (PLUGIN / 'proactive' / 'test_paths.py').exists()
    assert not (PLUGIN / 'tests' / '__init__.py').exists()
