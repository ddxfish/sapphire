"""core/instance.py: this Sapphire's id and name. The id is minted once per
data directory into the config dir (never user/), is never replaced, and is
never invented when it cannot be stored."""
import json
import threading

import pytest

from core import instance


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(instance, '_store_dir', tmp_path / 'instances')
    monkeypatch.setattr(instance, '_cache', {})
    return tmp_path / 'instances'


def test_a_test_never_mints_into_the_real_config_dir(monkeypatch):
    monkeypatch.setattr(instance, '_store_dir', None)
    monkeypatch.setattr(instance, '_cache', {})
    assert instance.instance_id() is None


def test_minted_once_and_read_back(store, monkeypatch):
    a = instance.instance_id()
    assert isinstance(a, str) and len(a) == 32
    assert len(list(store.glob('*.json'))) == 1 and not list(store.glob('*.tmp'))
    monkeypatch.setattr(instance, '_cache', {})          # a new process
    assert instance.instance_id() == a


def test_each_data_directory_is_its_own_sapphire(store, tmp_path):
    a = instance.instance_id(tmp_path / 'user')
    b = instance.instance_id(tmp_path / 'users' / 'second')
    assert a != b and instance.instance_id(tmp_path / 'user') == a


def test_an_unreadable_file_is_left_alone_and_no_id_is_invented(store, monkeypatch):
    a = instance.instance_id()
    f = next(store.glob('*.json'))
    f.write_text('{not json', encoding='utf-8')
    monkeypatch.setattr(instance, '_cache', {})
    assert instance.instance_id() is None
    assert f.read_text(encoding='utf-8') == '{not json'
    f.write_text(json.dumps({'id': a}), encoding='utf-8')
    assert instance.instance_id() == a


def test_two_minting_at_once_agree(store, monkeypatch):
    got = []

    def ask():
        got.append(instance.instance_id())
    for _ in range(4):                                   # four "processes": no shared cache
        monkeypatch.setattr(instance, '_cache', {})
        t = [threading.Thread(target=ask) for _ in range(4)]
        [x.start() for x in t]
        [x.join() for x in t]
    assert len(set(got)) == 1 and got[0]


def test_mint_never_overwrites(store):
    a = instance.instance_id()
    f = next(store.glob('*.json'))
    instance._mint(f, 'whatever')                        # a second minter arriving late
    assert json.loads(f.read_text(encoding='utf-8'))['id'] == a


def test_name_is_the_set_one_then_the_dashboard_one_then_the_hostname(monkeypatch):
    import config
    import socket
    monkeypatch.setattr(socket, 'gethostname', lambda: 'box-7')
    monkeypatch.setattr(config, 'INSTANCE_NAME', ' Purple ', raising=False)
    monkeypatch.setattr(config, 'DASHBOARD_DISPLAY_NAME', 'The Big Title', raising=False)
    assert instance.instance_name() == 'Purple'
    monkeypatch.setattr(config, 'INSTANCE_NAME', '', raising=False)
    assert instance.instance_name() == 'The Big Title'
    monkeypatch.setattr(config, 'DASHBOARD_DISPLAY_NAME', 'Sapphire', raising=False)   # the default
    assert instance.instance_name() == 'box-7'
    monkeypatch.setattr(config, 'DASHBOARD_DISPLAY_NAME', 'x' * 60, raising=False)
    assert instance.instance_name() == 'x' * 40
