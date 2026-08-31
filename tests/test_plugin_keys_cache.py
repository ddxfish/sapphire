"""Plugin authorized-keys cache — disk-cache-first contract (negspace E1, 2026-08-31).

_load_authorized_keys used to dial GitHub network-first on EVERY boot: the
disk cache was only a failure fallback and the in-memory TTL dies with the
process. Contract now: fresh disk cache (<24h) is authoritative and no
network call happens; stale/missing -> one fetch; fetch failure -> stale
cache beats nothing (and a broken SOCKS proxy must NOT fall back to a
direct connection — get_session raising means we serve from cache).
"""
import json
import time

import pytest

import core.plugin_verify as pv


@pytest.fixture
def fresh_pv(tmp_path, monkeypatch):
    monkeypatch.setattr(pv, '_CACHE_FILE', tmp_path / 'keys.json')
    monkeypatch.setattr(pv, '_authorized_keys_cache', None)
    monkeypatch.setattr(pv, '_authorized_keys_fetched_at', 0)
    return pv


def _write_cache(pv_mod, keys, age_s):
    pv_mod._CACHE_FILE.write_text(json.dumps(
        {"keys": keys, "fetched_at": time.time() - age_s}), encoding="utf-8")


def test_fresh_disk_cache_means_no_network(fresh_pv, monkeypatch):
    _write_cache(fresh_pv, [{"name": "a", "public_key_hex": "aa"}], age_s=60)
    def _boom():
        raise AssertionError("network fetch attempted with a fresh disk cache")
    monkeypatch.setattr(fresh_pv, '_fetch_remote_keys', _boom)
    keys = fresh_pv._load_authorized_keys()
    assert keys == [{"name": "a", "public_key_hex": "aa"}]


def test_stale_disk_cache_refreshes_from_remote(fresh_pv, monkeypatch):
    _write_cache(fresh_pv, [{"name": "old", "public_key_hex": "aa"}],
                 age_s=fresh_pv._CACHE_TTL + 60)
    monkeypatch.setattr(fresh_pv, '_fetch_remote_keys',
                        lambda: [{"name": "new", "public_key_hex": "bb"}])
    keys = fresh_pv._load_authorized_keys()
    assert keys == [{"name": "new", "public_key_hex": "bb"}]
    # and the refresh landed on disk
    on_disk = json.loads(fresh_pv._CACHE_FILE.read_text(encoding="utf-8"))
    assert on_disk["keys"] == [{"name": "new", "public_key_hex": "bb"}]


def test_fetch_failure_serves_stale_cache(fresh_pv, monkeypatch):
    """Fail-closed: remote down (or SOCKS enabled-but-broken -> get_session
    raises inside _fetch_remote_keys -> None) serves the stale cache, never
    a direct connection."""
    _write_cache(fresh_pv, [{"name": "old", "public_key_hex": "aa"}],
                 age_s=fresh_pv._CACHE_TTL + 60)
    monkeypatch.setattr(fresh_pv, '_fetch_remote_keys', lambda: None)
    keys = fresh_pv._load_authorized_keys()
    assert keys == [{"name": "old", "public_key_hex": "aa"}]


def test_nothing_anywhere_returns_empty(fresh_pv, monkeypatch):
    monkeypatch.setattr(fresh_pv, '_fetch_remote_keys', lambda: None)
    assert fresh_pv._load_authorized_keys() == []


def test_memory_cache_still_short_circuits(fresh_pv, monkeypatch):
    monkeypatch.setattr(fresh_pv, '_authorized_keys_cache', [{"name": "m", "public_key_hex": "cc"}])
    monkeypatch.setattr(fresh_pv, '_authorized_keys_fetched_at', time.time())
    def _boom():
        raise AssertionError("network fetch attempted with fresh memory cache")
    monkeypatch.setattr(fresh_pv, '_fetch_remote_keys', _boom)
    assert fresh_pv._load_authorized_keys() == [{"name": "m", "public_key_hex": "cc"}]
