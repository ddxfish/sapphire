"""The wallet store's rules: a key is never written over without being named,
and a key this machine cannot decrypt is not mistaken for no key.

The store here is a stand-in with the credentials manager's four wallet
methods; the real one is never imported.
"""
import sys
from types import SimpleNamespace

import pytest

bit = pytest.importorskip('bit')

from plugins.bitcoin import wallets                 # noqa: E402

BAD_WIF = '5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTK'     # a real key, last character changed


class Creds:
    """get/set/delete/list as core/credentials_manager.py has them. A scope in
    `locked` reads as '' the way a key that will not decrypt does."""
    def __init__(self):
        self.wallets, self.locked, self.disk_ok = {}, set(), True

    def get_bitcoin_wallet(self, scope='default'):
        w = self.wallets.get(scope, {})
        return {'label': w.get('label', scope), 'wif': '' if scope in self.locked else w.get('wif', '')}

    def list_bitcoin_wallets(self):
        out = []
        for scope in self.wallets:
            wif, address = self.get_bitcoin_wallet(scope)['wif'], ''
            if wif:
                try:
                    address = bit.Key(wif).address
                except Exception:
                    address = '(invalid key)'
            out.append({'scope': scope, 'label': self.wallets[scope]['label'], 'address': address})
        return out

    def set_bitcoin_wallet(self, scope, wif, label=''):
        self.wallets[scope] = {'label': label or scope, 'wif': wif}     # memory first, like the real one
        self.locked.discard(scope)
        return self.disk_ok

    def delete_bitcoin_wallet(self, scope):
        if scope not in self.wallets:
            return False
        del self.wallets[scope]
        return self.disk_ok


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setattr(wallets, '_changed', lambda scope, action: None)
    return Creds()


def _with_wallet(creds, scope='main', label='Main'):
    key = bit.Key()
    creds.wallets[scope] = {'label': label, 'wif': key.to_wif()}
    return key


# ── reading ──────────────────────────────────────────────────────────────

def test_look_tells_the_four_states_apart(creds):
    key = _with_wallet(creds)
    assert wallets.look(creds, 'nope')['state'] == 'missing'
    found = wallets.look(creds, 'main')
    assert (found['state'], found['address'], found['label']) == ('ok', key.address, 'Main')
    creds.locked.add('main')
    found = wallets.look(creds, 'main')
    assert found['state'] == 'unreadable' and found['key'] is None      # stored, not gone
    creds.wallets['odd'] = {'label': 'odd', 'wif': BAD_WIF}
    assert wallets.look(creds, 'odd')['state'] == 'invalid'


def test_a_bad_stored_key_is_never_logged(creds, caplog):
    creds.wallets['odd'] = {'label': 'odd', 'wif': BAD_WIF}
    with caplog.at_level('DEBUG'):
        wallets.look(creds, 'odd')
    assert BAD_WIF not in caplog.text


# ── making ───────────────────────────────────────────────────────────────

def test_generate_makes_a_new_wallet_and_never_replaces_one(creds):
    out, status = wallets.save(creds, 'fresh', generate=True, label='  New   one ')
    assert status == 200 and out['created'] and out['label'] == 'New one'
    assert wallets.look(creds, 'fresh')['address'] == out['address']

    kept = dict(creds.wallets['fresh'])
    out, status = wallets.save(creds, 'fresh', generate=True)
    assert status == 409 and 'never replaces' in out['error'] and creds.wallets['fresh'] == kept

    creds.locked.add('fresh')                           # the wallet that shows as "(no key)"
    out, status = wallets.save(creds, 'fresh', generate=True)
    assert status == 409 and creds.wallets['fresh'] == kept


def test_a_pasted_key_makes_a_wallet(creds):
    key = bit.Key()
    out, status = wallets.save(creds, 'mine', wif=f"  {key.to_wif()}\n")
    assert status == 200 and out['address'] == key.address and out['created'] and out['label'] == 'mine'


@pytest.mark.parametrize('name', ['none', 'Has Space', 'UPPER', '', 'a/b', '<b>', 'x' * 65])
def test_wallet_names(creds, name):
    out, status = wallets.save(creds, name, generate=True)
    assert status == 400 and not creds.wallets


def test_a_mistyped_key_is_not_repeated_back(creds):
    out, status = wallets.save(creds, 'mine', wif=BAD_WIF)
    assert status == 400 and BAD_WIF not in str(out) and BAD_WIF[:10] not in str(out) and not creds.wallets
    assert wallets.save(creds, 'mine')[1] == 400        # no key at all


# ── replacing ────────────────────────────────────────────────────────────

def test_another_key_over_a_wallet_must_name_the_one_it_replaces(creds):
    old, new = _with_wallet(creds), bit.Key()
    out, status = wallets.save(creds, 'main', wif=new.to_wif())
    assert status == 409 and out['needs_replace']
    assert (out['old_address'], out['new_address']) == (old.address, new.address)
    assert wallets.look(creds, 'main')['address'] == old.address

    out, status = wallets.save(creds, 'main', wif=new.to_wif(), replace=bit.Key().address)   # a stale page's idea of the old key
    assert status == 409 and wallets.look(creds, 'main')['address'] == old.address

    out, status = wallets.save(creds, 'main', wif=new.to_wif(), replace=old.address)
    assert status == 200 and not out['created'] and wallets.look(creds, 'main')['address'] == new.address
    assert wallets.look(creds, 'main')['label'] == 'Main'           # the nickname stays


def test_the_same_key_again_needs_no_asking(creds):
    key = _with_wallet(creds)
    out, status = wallets.save(creds, 'main', wif=key.to_wif(), label='Renamed')
    assert status == 200 and wallets.look(creds, 'main')['label'] == 'Renamed'


def test_a_rename_keeps_the_key(creds):
    key = _with_wallet(creds)
    out, status = wallets.save(creds, 'main', label='Savings')
    found = wallets.look(creds, 'main')
    assert status == 200 and (found['label'], found['address']) == ('Savings', key.address)
    assert wallets.save(creds, 'main', label='x' * 200)[0]['label'] == 'x' * wallets.LABEL_MAX


def test_an_unreadable_key_is_not_written_over_by_accident(creds):
    _with_wallet(creds)
    creds.locked.add('main')
    kept = dict(creds.wallets['main'])

    out, status = wallets.save(creds, 'main', label='Savings')      # a rename would re-save an empty key
    assert status == 409 and creds.wallets['main'] == kept
    backup = bit.Key()
    out, status = wallets.save(creds, 'main', wif=backup.to_wif())
    assert status == 409 and out['needs_replace'] and out['state'] == 'unreadable'
    assert out['new_address'] == backup.address and creds.wallets['main'] == kept
    assert wallets.save(creds, 'main', wif=backup.to_wif(), replace='anything')[1] == 409

    out, status = wallets.save(creds, 'main', wif=backup.to_wif(), replace='unreadable')
    assert status == 200 and wallets.look(creds, 'main')['address'] == backup.address


# ── a disk that says no ──────────────────────────────────────────────────

def test_a_failed_write_leaves_the_old_key_in_use(creds):
    old = _with_wallet(creds)
    creds.disk_ok = False
    out, status = wallets.save(creds, 'main', wif=bit.Key().to_wif(), replace=old.address)
    assert status == 500 and 'Nothing was saved' in out['error']
    assert creds.wallets['main']['wif'] == old.to_wif()             # not the key that never reached the disk


def test_a_failed_write_leaves_no_wallet_that_is_only_in_memory(creds):
    creds.disk_ok = False
    out, status = wallets.save(creds, 'ghost', generate=True)
    assert status == 500 and 'ghost' not in creds.wallets           # one to fund today and lose at restart


# ── deleting ─────────────────────────────────────────────────────────────

def test_delete_releases_everyone_who_named_the_wallet(creds, monkeypatch):
    swept = []
    monkeypatch.setitem(sys.modules, 'core.chat.scope_cleanup',
                        SimpleNamespace(sweep_orphaned_scope_ref=lambda key, name: swept.append((key, name))))
    _with_wallet(creds)
    assert wallets.remove(creds, 'main') == ({"success": True}, 200)
    assert swept == [('bitcoin_scope', 'main')] and 'main' not in creds.wallets
    assert wallets.remove(creds, 'main')[1] == 404 and len(swept) == 1


# ── the page's doors ─────────────────────────────────────────────────────

@pytest.fixture
def doors():
    import plugins.bitcoin.routes.wallets as routes
    return routes


def test_save_door_hands_the_body_to_the_rules(doors, creds):
    old, new = _with_wallet(creds), bit.Key()
    out, status = doors.save('main', body={'wif': new.to_wif(), 'generate': 'true'}, credentials=creds,
                             settings={}, query={}, request=None)
    assert status == 409 and out['old_address'] == old.address      # 'true' is not True: nothing generated
    out, status = doors.save('main', body={'wif': new.to_wif(), 'replace': old.address}, credentials=creds)
    assert status == 200 and 'wif' not in out
    assert doors.save('x', body=None, credentials=creds)[1] == 400


def test_check_door_adds_up_the_coins(doors, creds, monkeypatch):
    from bit.network.meta import Unspent
    _with_wallet(creds)
    monkeypatch.setattr(bit.PrivateKey, 'get_unspents',
                        lambda self: [Unspent(1500, 6, '00', 'aa' * 32, 0), Unspent(500, 6, '00', 'bb' * 32, 1)])
    out = doors.check('main', credentials=creds)
    assert (out['scope'], out['balance_sat'], out['balance_btc'], out['coins']) == ('main', 2000, '0.00002000', 2)

    def down(self):
        raise ConnectionError("All APIs are unreachable.")
    monkeypatch.setattr(bit.PrivateKey, 'get_unspents', down)
    assert doors.check('main', credentials=creds)[1] == 502
    creds.locked.add('main')
    assert doors.check('main', credentials=creds)[1] == 409


def test_sends_door_gives_the_wallets_own_rows_without_the_raw_transaction(doors, monkeypatch):
    from plugins.bitcoin import send
    rows = [{'id': str(i), 'ts': 1000 + i, 'scope': 'main' if i % 2 else 'other', 'to': f"addr{i}", 'sat': 1000 * i,
             'fee': 200, 'txid': f"{i:064x}", 'hex': 'deadbeef', 'status': 'sent', 'chat': 'trinity'} for i in range(1, 7)]
    monkeypatch.setattr(send, '_state', lambda: SimpleNamespace(get=lambda key, default=None: rows))
    out = doors.sends('main')
    assert [r['to'] for r in out['sends']] == ['addr5', 'addr3', 'addr1']       # newest first
    assert out['sends'][0]['btc'] == '0.00005000' and 'deadbeef' not in str(out)
