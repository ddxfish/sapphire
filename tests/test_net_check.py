"""core/net_check.py: why a download host cannot be reached, in a verdict and
in words. The network is faked at the module's three seams (_local, _public,
_try). Run with: pytest tests/test_net_check.py -v"""
import pytest

from core import net_check as nc

HOST = 'cas-server.example'


@pytest.fixture
def net(monkeypatch):
    """Set what the network says: net(local=[...], public=[...]|None, answers={ip: kind})."""
    monkeypatch.setattr(nc.urllib.request, 'getproxies', lambda: {})
    import config
    monkeypatch.setattr(config, 'SOCKS_ENABLED', False, raising=False)

    def _set(local, public, answers):
        monkeypatch.setattr(nc, '_local', lambda host: list(local))
        monkeypatch.setattr(nc, '_public', lambda host, wait=nc.WAIT: None if public is None else list(public))
        monkeypatch.setattr(nc, '_try', lambda host, ip, wait=nc.WAIT: answers.get(ip, 'timeout'))
    return _set


def test_a_host_that_answers_is_ok_and_the_public_server_is_never_asked(net, monkeypatch):
    net(['1.0.0.1', '1.0.0.2'], None, {'1.0.0.1': 'timeout', '1.0.0.2': 'ok'})
    monkeypatch.setattr(nc, '_public', lambda *a, **k: pytest.fail("asked a third party about a healthy host"))
    assert nc.diagnose(HOST)['verdict'] == 'ok'


def test_dead_local_addresses_and_live_public_ones_is_stale_dns(net):
    """The 2026-11-24 router: four addresses time out, four answer as somebody else."""
    net(['1.0.0.1', '1.0.0.2'], ['2.0.0.1', '2.0.0.2'],
        {'1.0.0.1': 'timeout', '1.0.0.2': 'wrong-server', '2.0.0.1': 'ok', '2.0.0.2': 'ok'})
    d = nc.diagnose(HOST)
    assert d['verdict'] == 'stale-dns' and 'timeout, wrong-server' in d['detail']
    verdict, words = nc.check([HOST])
    assert verdict == 'stale-dns' and HOST in words and 'router' in words and '1.1.1.1' in words


def test_dead_everywhere_is_their_outage(net):
    net(['1.0.0.1'], ['1.0.0.1', '2.0.0.1'], {})
    assert nc.diagnose(HOST)['verdict'] == 'host-down'


def test_no_local_answer_but_a_public_one_is_no_dns(net):
    net([], ['2.0.0.1'], {'2.0.0.1': 'ok'})
    assert nc.diagnose(HOST)['verdict'] == 'no-dns'


def test_nothing_looked_up_and_nobody_to_ask_is_offline(net):
    net([], None, {})
    assert nc.diagnose(HOST)['verdict'] == 'offline'


def test_dead_addresses_and_nobody_to_ask_is_cut_off(net):
    net(['1.0.0.1'], None, {})
    assert nc.diagnose(HOST)['verdict'] == 'cut-off'


def test_a_certificate_out_of_date_range_blames_the_clock(net):
    net(['1.0.0.1'], ['2.0.0.1'], {'1.0.0.1': 'clock'})
    verdict, words = nc.check([HOST])
    assert verdict == 'clock' and 'date or time' in words


def test_behind_a_proxy_it_says_nothing(net, monkeypatch):
    net(['1.0.0.1'], ['2.0.0.1'], {})
    monkeypatch.setattr(nc.urllib.request, 'getproxies', lambda: {'https': 'http://proxy:3128'})
    assert nc.check([HOST]) == ('unknown', '')


@pytest.mark.parametrize('proxies, socks', [({'all': 'socks5h://127.0.0.1:1080'}, False), ({}, True)])
def test_socks_mode_never_looks_anything_up(net, monkeypatch, proxies, socks):
    """SOCKS mode promises no hostname reaches the local resolver (core/net.py):
    the check stands down before its first lookup, and asks no public server."""
    import config
    net(['1.0.0.1'], ['2.0.0.1'], {})
    monkeypatch.setattr(nc.urllib.request, 'getproxies', lambda: proxies)
    monkeypatch.setattr(config, 'SOCKS_ENABLED', socks, raising=False)
    for seam in ('_local', '_public', '_try'):
        monkeypatch.setattr(nc, seam, lambda *a, **k: pytest.fail("looked directly while proxied"))
    assert nc.check([HOST]) == ('unknown', '')


def test_several_hosts_the_row_says_the_most_telling(net, monkeypatch):
    net([], [], {})
    monkeypatch.setattr(nc, 'diagnose', lambda h: {'host': h, 'detail': '',
                                                   'verdict': 'stale-dns' if h == 'bad.example' else 'ok'})
    verdict, words = nc.check(['good.example', 'bad.example', 'good.example'])
    assert verdict == 'stale-dns' and 'bad.example' in words


def test_all_fine_says_slow_or_filtered(net, monkeypatch):
    monkeypatch.setattr(nc, 'diagnose', lambda h: {'host': h, 'detail': '', 'verdict': 'ok'})
    assert nc.check(['a.example']) == ('ok', "The servers answer, so the connection is likely slow or being filtered.")


def test_no_hosts_no_words():
    assert nc.check([]) == ('unknown', '') and nc.why(None) == ''
