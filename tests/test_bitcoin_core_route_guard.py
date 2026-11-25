"""The core wallet route never replaces a key (2026-11-25 docs hunt).

PUT /api/bitcoin/wallets/{scope} wrote whatever it was given over the key that
was there: `generate: true` on a wallet that already held coins made a new key
in its place, and the old one was gone. Its 400 also carried the library's
error text, which quotes what was typed. The Bitcoin plugin's own route asks
before a key is replaced (core/plugins/bitcoin/wallets.py); this one cannot
ask, so it refuses.
"""
import pytest

bit = pytest.importorskip("bit")


@pytest.fixture
def wallets(monkeypatch):
    """An in-memory wallet store in place of the real credentials file."""
    from core.credentials_manager import credentials
    store = {}
    monkeypatch.setattr(credentials, "get_bitcoin_wallet",
                        lambda scope: dict(store.get(scope, {'label': scope, 'wif': ''})), raising=False)
    monkeypatch.setattr(credentials, "list_bitcoin_wallets",
                        lambda: [{'scope': s, 'label': w['label']} for s, w in store.items()], raising=False)

    def _set(scope, wif, label=''):
        store[scope] = {'label': label or scope, 'wif': wif}
        return True
    monkeypatch.setattr(credentials, "set_bitcoin_wallet", _set, raising=False)
    return store


def _put(client, scope, **body):
    c, csrf = client
    return c.put(f"/api/bitcoin/wallets/{scope}", headers={"X-CSRF-Token": csrf}, json=body)


def test_a_new_wallet_is_made_from_a_key_or_generated(client, wallets):
    key = bit.Key()
    r = _put(client, "savings", wif=key.to_wif(), label="Savings")
    assert r.status_code == 200, r.text
    assert r.json()["address"] == key.address
    assert wallets["savings"] == {'label': 'Savings', 'wif': key.to_wif()}

    r = _put(client, "fresh", generate=True)
    assert r.status_code == 200, r.text
    assert bit.Key(wallets["fresh"]['wif']).address == r.json()["address"]


def test_generate_never_replaces_the_key_of_a_wallet_that_exists(client, wallets):
    old = bit.Key().to_wif()
    wallets["savings"] = {'label': 'Savings', 'wif': old}
    r = _put(client, "savings", generate=True)
    assert r.status_code == 409, r.text
    assert wallets["savings"]['wif'] == old


def test_a_different_key_never_replaces_the_one_that_is_there(client, wallets):
    old, new = bit.Key().to_wif(), bit.Key().to_wif()
    wallets["savings"] = {'label': 'Savings', 'wif': old}
    r = _put(client, "savings", wif=new)
    assert r.status_code == 409, r.text
    assert wallets["savings"]['wif'] == old
    assert new not in r.text and old not in r.text


@pytest.mark.parametrize("body", [{'generate': True}, {'wif': None}])
def test_a_key_this_machine_cannot_read_is_not_written_over(client, wallets, body):
    """Listed, but its key reads as '': it is still in the file and still
    recoverable, until something is saved over it."""
    wallets["savings"] = {'label': 'Savings', 'wif': ''}
    if 'wif' in body:
        body = {'wif': bit.Key().to_wif()}
    r = _put(client, "savings", **body)
    assert r.status_code == 409, r.text
    assert wallets["savings"]['wif'] == ''


def test_a_rename_and_the_same_key_again_still_work(client, wallets):
    old = bit.Key().to_wif()
    wallets["savings"] = {'label': 'Savings', 'wif': old}
    r = _put(client, "savings", label="Rainy day")
    assert r.status_code == 200, r.text
    assert wallets["savings"] == {'label': 'Rainy day', 'wif': old}
    r = _put(client, "savings", wif=old, label="Again")
    assert r.status_code == 200, r.text
    assert wallets["savings"] == {'label': 'Again', 'wif': old}


def test_a_bad_key_is_refused_without_repeating_what_was_typed(client, wallets):
    typed = bit.Key().to_wif()[:-4] + "zzzz"        # most of a real key, bad checksum
    r = _put(client, "savings", wif=typed)
    assert r.status_code == 400, r.text
    assert typed not in r.text and typed[:20] not in r.text
    assert "savings" not in wallets
