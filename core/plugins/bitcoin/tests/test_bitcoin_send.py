"""Sending: the rails, the ledger, the fee and the broadcast.

Nothing here reaches a network, the credential store, the notification center
or the real ledger file: the wallet is a key made here with made-up coins, and
the explorers, the ledger store and the notifier are stand-ins.
"""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

bit = pytest.importorskip('bit')

from bit.network.meta import Unspent                         # noqa: E402
from bit.transaction import address_to_scriptpubkey, deserialize   # noqa: E402

from plugins.bitcoin import send                             # noqa: E402
from plugins.bitcoin.tools import bitcoin_tool               # noqa: E402

PLUGIN = Path(send.__file__).parent
CORE = PLUGIN.parent.parent
REAL_LANE = send.lane


class Store:
    """PluginState's get/save, in memory."""
    def __init__(self):
        self.data, self.fail = {}, False

    def get(self, key, default=None):
        return json.loads(json.dumps(self.data.get(key, default)))

    def save(self, key, value):
        if self.fail:
            raise OSError("disk full")
        self.data[key] = json.loads(json.dumps(value))


class Net:
    """The explorers. `answers` are given out in order: a (status, text) or an exception."""
    def __init__(self):
        self.answers, self.posted, self.seen = [('sent', '')], [], None


def _wallet(*amounts):
    key = bit.Key()
    script = address_to_scriptpubkey(key.address).hex()
    coins = [Unspent(a, 6, script, f"{i + 1:02x}" * 32, 0, type='p2pkh') for i, a in enumerate(amounts)]
    key.get_unspents = lambda: list(coins)
    return key


@pytest.fixture
def world(monkeypatch):
    """A funded wallet, a person's turn, a 3 sat/byte network that takes everything."""
    w = SimpleNamespace(key=_wallet(50_000, 70_000, 2_000_000), dest=bit.Key().address, store=Store(),
                        net=Net(), told=[], settings={'max_send_btc': '0.01', 'max_day_btc': '0.02'})

    def broadcast(tx_hex):
        w.net.posted.append((tx_hex, [r['status'] for r in send.ledger(w.store)]))
        return w.net.answers.pop(0) if len(w.net.answers) > 1 else w.net.answers[0]
    monkeypatch.setattr(send, 'lane', lambda: 'person')
    monkeypatch.setattr(send, 'fee_rate', lambda: 3)
    monkeypatch.setattr(send, 'broadcast', broadcast)
    monkeypatch.setattr(send, 'seen', lambda txid: w.net.seen)
    monkeypatch.setattr(send, '_tell_user', lambda row, title, level='info': w.told.append((title, level, row['txid'])))
    monkeypatch.setattr(send, '_state', lambda: w.store)
    w.send = lambda amount='0.001', to=None, **s: send.send(
        w.key, 'main', w.dest if to is None else to, amount, settings={**w.settings, **s}, state=w.store, chat='trinity')
    return w


def _rows(w):
    return send.ledger(w.store)


# ── a send that goes out ─────────────────────────────────────────────────

def test_send_goes_out_and_says_what_was_paid(world):
    text, ok = world.send('0.001')
    [row] = _rows(world)
    assert ok and row['status'] == 'sent' and row['sat'] == 100_000
    assert f"Sent 0.00100000 BTC to {world.dest}" in text
    assert f"Transaction: {row['txid']}" in text and f"Fee: {row['fee']} satoshi (3 sat/byte)" in text
    assert world.told == [("Sent 0.00100000 BTC", 'info', row['txid'])]
    assert (row['chat'], row['lane'], row['scope']) == ('trinity', 'person', 'main')


def test_the_ledger_has_the_transaction_before_it_is_broadcast(world):
    world.send()
    [(tx_hex, statuses)] = world.net.posted
    assert statuses == ['signed']                      # on disk first: a stop or a crash cannot lose the id
    assert _rows(world)[0]['hex'] == tx_hex


def test_what_is_signed_pays_the_address_asked_and_the_change_comes_home(world):
    world.send('0.001')
    tx = deserialize(_rows(world)[0]['hex'])
    outs = [(int.from_bytes(o.amount, 'little'), o.script_pubkey) for o in tx.TxOut]
    assert outs[0] == (100_000, address_to_scriptpubkey(world.dest))
    assert outs[1][1] == address_to_scriptpubkey(world.key.address)
    assert len(tx.TxIn) < 3                             # the coins the payment needs, not every coin in the wallet


@pytest.mark.parametrize('typed,sat', [('0.29', 29_000_000), (0.29, 29_000_000), ('0.57', 57_000_000),
                                       ('0.00000546', 546), (' 0.001 ', 100_000)])
def test_amounts_are_exact(typed, sat):
    assert send.satoshi(typed) == sat                   # int(float('0.29') * 1e8) is 28,999,999


@pytest.mark.parametrize('bad', [True, False, None, '', 'NaN', 'Infinity', '-1', '0', '0.000000001', 'all', '1e99'])
def test_bad_amounts_send_nothing(world, bad):
    text, ok = world.send(bad)
    assert not ok and not world.net.posted and not _rows(world)


# ── who asked ────────────────────────────────────────────────────────────

def test_a_turn_no_person_started_sends_nothing(world, monkeypatch):
    monkeypatch.setattr(send, 'lane', lambda: 'a message over MCP')
    text, ok = world.send()
    assert not ok and 'a message over MCP' in text and 'Nothing was sent' in text
    assert not world.net.posted and not _rows(world)


def test_the_user_can_allow_machine_turns(world, monkeypatch):
    monkeypatch.setattr(send, 'lane', lambda: 'a task or an agent')
    assert world.send(machine_sends=True)[1]
    assert _rows(world)[0]['lane'] == 'a task or an agent'


@pytest.mark.parametrize('value', ['true', 1, 'yes', None])
def test_only_a_real_true_allows_machine_turns(value):
    assert send.limits({'machine_sends': value})['machine_sends'] is False


def _lane_with(monkeypatch, ctx, streams=(), answering=False):
    from core import mcp_server
    from core.chat import function_manager as fm
    system = SimpleNamespace(llm_chat=SimpleNamespace(streams_for_chat=lambda chat: list(streams)))
    monkeypatch.setitem(sys.modules, 'core.api_fastapi', SimpleNamespace(get_system=lambda: system))
    t1, t2 = fm.tool_context.set(ctx), mcp_server.answering.set(answering)
    try:
        return REAL_LANE()
    finally:
        fm.tool_context.reset(t1)
        mcp_server.answering.reset(t2)


def _stream(source='absent', llm_done=False):
    s = SimpleNamespace(llm_done=llm_done)
    if source != 'absent':
        s.turn_source = source
    return s


def test_lane_is_a_person_only_when_it_can_be_told(monkeypatch):
    chat = {'chat': 'trinity', 'persona': 'sapphire'}
    assert _lane_with(monkeypatch, chat, [_stream()]) == 'person'                     # typed, or the wake word
    assert _lane_with(monkeypatch, chat, [_stream('device:kitchen')]) == 'person'
    assert _lane_with(monkeypatch, chat, [_stream(), _stream(llm_done=True)]) == 'person'   # an old turn's voice tail
    assert _lane_with(monkeypatch, chat, [_stream('agent:research')]) == 'a machine turn (agent)'
    assert _lane_with(monkeypatch, chat, [_stream('cadence')]) == 'a machine turn (cadence)'
    assert _lane_with(monkeypatch, chat, [_stream()], answering=True) == 'a message over MCP'
    assert _lane_with(monkeypatch, {**chat, 'channel': 'continuity'}, [_stream()]) == 'a task or an agent'
    for unsure in ((None, [_stream()]), ({'persona': 'x'}, [_stream()]), (chat, []), (chat, [_stream(), _stream()])):
        assert _lane_with(monkeypatch, *unsure) == 'an unknown lane'


def test_lane_fails_closed_when_core_cannot_be_asked(monkeypatch):
    from core.chat import function_manager as fm
    monkeypatch.setitem(sys.modules, 'core.api_fastapi', SimpleNamespace())      # no get_system
    token = fm.tool_context.set({'chat': 'trinity'})
    try:
        assert REAL_LANE() == 'an unknown lane'
    finally:
        fm.tool_context.reset(token)


def test_core_still_marks_turns_the_way_lane_reads_them():
    """lane() reads three things core sets. If one is renamed, a missing mark
    would read as 'a person typed this', so the names are pinned here."""
    assert "stream.turn_source = source" in (CORE / 'cadence.py').read_text(encoding='utf-8')
    assert "channel='continuity'" in (CORE / 'continuity' / 'execution_context.py').read_text(encoding='utf-8')
    chat = (CORE / 'chat' / 'chat.py').read_text(encoding='utf-8')
    assert "def streams_for_chat(self, chat_name)" in chat
    assert "self.llm_done = False" in (CORE / 'chat' / 'chat_streaming.py').read_text(encoding='utf-8')
    from core import idle, mcp_server
    assert idle.is_person(None) and idle.is_person('device:x') and idle.is_person('web')
    assert not any(idle.is_person(s) for s in ('mcp:claude', 'agent:research', 'cadence', 'midi', 'continuity:x'))
    assert mcp_server.answering.get() is False


# ── the user's limits ────────────────────────────────────────────────────

def test_over_the_per_send_limit_sends_nothing(world):
    text, ok = world.send('0.011')
    assert not ok and 'limit of 0.01000000 BTC per send' in text and not world.net.posted


def test_the_day_limit_counts_what_left_and_what_may_have(world):
    now = time.time()
    world.store.save('sends', [
        {'id': 'a', 'ts': now - 3600, 'scope': 'main', 'to': 'x', 'sat': 900_000, 'status': 'sent', 'txid': 't1'},
        {'id': 'b', 'ts': now - 7200, 'scope': 'main', 'to': 'y', 'sat': 500_000, 'status': 'failed', 'txid': 't2'},
        {'id': 'c', 'ts': now - 90000, 'scope': 'main', 'to': 'z', 'sat': 900_000, 'status': 'sent', 'txid': 't3'},
        {'id': 'd', 'ts': now - 60, 'scope': 'other', 'to': 'x', 'sat': 900_000, 'status': 'sent', 'txid': 't4'},
    ])
    assert send.spent_today(_rows(world), 'main') == 900_000       # not the failed one, yesterday's, or another wallet's
    assert world.send('0.01')[1]                                    # 0.009 + 0.01 = 0.019
    text, ok = world.send('0.002')                                  # 0.019 + 0.002 > 0.02
    assert not ok and 'a day' in text and len(world.net.posted) == 1


def test_zero_is_no_limit_and_rubbish_is_the_default():
    assert send.limits({'max_send_btc': '0', 'max_day_btc': 0})['send'] == 0
    assert send.limits({'max_send_btc': '0', 'max_day_btc': 0})['day'] == 0
    for rubbish in (None, '', 'lots', -1, 'NaN', [], True):
        lim = send.limits({'max_send_btc': rubbish, 'max_day_btc': rubbish, 'max_fee_rate': rubbish})
        assert (lim['send'], lim['day'], lim['fee_rate']) == (500_000, 2_000_000, 100)
    assert send.limits(None) == {'send': 500_000, 'day': 2_000_000, 'fee_rate': 100, 'machine_sends': False}
    assert send.limits({'max_send_btc': 0.005})['send'] == 500_000


def test_manifest_defaults_are_the_ones_the_code_falls_back_to():
    manifest = json.loads((PLUGIN / 'plugin.json').read_text(encoding='utf-8'))
    declared = {s['key']: s['default'] for s in manifest['capabilities']['settings']}
    assert declared == send.DEFAULTS
    assert all(s.get('hidden') is True for s in manifest['capabilities']['settings'])      # the wallet page owns them


def test_a_chat_with_no_wallet_picked_has_no_wallet():
    """The scope's fallback is None, never the real wallet named 'default'."""
    [scope] = json.loads((PLUGIN / 'plugin.json').read_text(encoding='utf-8'))['capabilities']['scopes']
    assert 'default' in scope and scope['default'] is None


# ── never twice ──────────────────────────────────────────────────────────

def test_the_same_payment_is_not_sent_twice(world):
    assert world.send('0.001')[1]
    text, ok = world.send('0.001')
    assert not ok and 'already went out' in text and _rows(world)[0]['txid'] in text
    assert len(world.net.posted) == 1
    assert world.send('0.0011')[1]                      # another amount is another payment


def test_the_same_payment_goes_again_after_the_wait(world):
    world.send('0.001')
    rows = _rows(world)
    rows[0]['ts'] -= send.REPEAT_S + 1
    world.store.save('sends', rows)
    assert world.send('0.001')[1]


def test_a_refused_broadcast_says_nothing_left_and_may_be_tried_again(world):
    world.net.answers = [('failed', 'min relay fee not met'), ('sent', '')]
    text, ok = world.send('0.001')
    assert not ok and 'refused the transaction (min relay fee not met)' in text and 'safe to try again' in text
    assert _rows(world)[0]['status'] == 'failed' and not world.told
    assert world.send('0.001')[1]


def test_an_unanswered_broadcast_is_not_called_a_failure(world):
    world.net.answers = [('unknown', '')]
    text, ok = world.send('0.001')
    [row] = _rows(world)
    assert not ok and row['status'] == 'unknown'
    assert 'NOT CONFIRMED EITHER WAY' in text and row['txid'] in text and 'Do not send it again' in text
    assert world.told == [("A Bitcoin send may or may not have gone out", 'warning', row['txid'])]


def test_an_unanswered_broadcast_that_an_explorer_then_shows_is_sent(world):
    world.net.answers, world.net.seen = [('unknown', '')], True
    assert world.send('0.001')[1] and _rows(world)[0]['status'] == 'sent'


def test_the_wallet_waits_while_a_send_is_not_known(world):
    world.net.answers = [('unknown', ''), ('sent', '')]
    world.send('0.001')
    text, ok = world.send('0.0005', to=bit.Key().address)           # a different payment: still held
    assert not ok and 'may or may not have gone out' in text and len(world.net.posted) == 1

    world.net.seen = True                                           # the network shows it: it went out
    assert world.send('0.0005', to=bit.Key().address)[1]
    assert [r['status'] for r in _rows(world)] == ['sent', 'sent']


def test_a_send_nobody_ever_saw_is_let_go_after_the_hold(world):
    world.net.answers = [('unknown', ''), ('sent', '')]
    world.send('0.001')
    world.net.seen = False                                          # both explorers: no such transaction
    assert not world.send('0.0005')[1]                              # too soon to be sure
    rows = _rows(world)
    rows[0]['ts'] -= send.UNSURE_HOLD_S + 1
    world.store.save('sends', rows)
    assert world.send('0.0005')[1]
    assert [r['status'] for r in _rows(world)] == ['failed', 'sent']


def test_a_send_cut_off_between_ledger_and_broadcast_holds_the_wallet(world):
    world.store.save('sends', [{'id': 'a', 'ts': time.time() - 5, 'scope': 'main', 'to': 'x', 'sat': 1000,
                                'status': 'signed', 'txid': 'ab' * 32}])
    assert not world.send('0.001')[1] and not world.net.posted


def test_no_ledger_no_send(world):
    world.store.fail = True
    text, ok = world.send('0.001')
    assert not ok and 'could not be written to the ledger' in text and not world.net.posted


# ── the fee ──────────────────────────────────────────────────────────────

def test_no_fee_answer_sends_nothing(world, monkeypatch):
    monkeypatch.setattr(send, 'fee_rate', lambda: None)
    text, ok = world.send()
    assert not ok and 'fee could not be looked up' in text and not world.net.posted and not _rows(world)


def test_a_fee_over_the_limit_sends_nothing(world, monkeypatch):
    monkeypatch.setattr(send, 'fee_rate', lambda: 101)
    text, ok = world.send()
    assert not ok and '101 sat/byte' in text and not world.net.posted
    assert world.send(max_fee_rate=150)[1]


class _Reply:
    def __init__(self, status, body='', text=''):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        return self._body


def _net(monkeypatch, get=None, post=None):
    from core import net
    calls = []

    def answer(table, url, **kw):
        calls.append((url, kw))
        got = next(v for k, v in table.items() if k in url)
        if isinstance(got, Exception):
            raise got
        return got
    monkeypatch.setattr(net, 'get', lambda url, **kw: answer(get, url, **kw))
    monkeypatch.setattr(net, 'post', lambda url, **kw: answer(post, url, **kw))
    return calls


def test_fee_lookup_has_a_timeout_and_a_second_source(monkeypatch):
    calls = _net(monkeypatch, get={'mempool.space': _Reply(200, {'fastestFee': 7})})
    assert send.fee_rate() == 7 and calls[0][1]['timeout'] <= 10
    _net(monkeypatch, get={'mempool.space': TimeoutError(), 'blockstream': _Reply(200, {'2': 4.2})})
    assert send.fee_rate() == 5                         # rounded up
    _net(monkeypatch, get={'mempool.space': _Reply(200, {'fastestFee': 0})})
    assert send.fee_rate() == 1
    _net(monkeypatch, get={'mempool.space': _Reply(503), 'blockstream': ConnectionError()})
    assert send.fee_rate() is None                      # no guess: bit's own fallback was 72 sat/byte


# ── the broadcast and the look-up ────────────────────────────────────────

def test_broadcast_tells_sent_refused_and_unknown_apart(monkeypatch):
    calls = _net(monkeypatch, post={'mempool.space': _Reply(200, text='ab' * 32)})
    assert send.broadcast('00ff') == ('sent', '') and len(calls) == 1 and calls[0][1]['data'] == '00ff'

    _net(monkeypatch, post={'mempool.space': _Reply(400, text='bad-txns-inputs-missingorspent'),
                            'blockstream': _Reply(400, text='bad-txns-inputs-missingorspent')})
    assert send.broadcast('00ff') == ('failed', 'bad-txns-inputs-missingorspent')

    for first in (TimeoutError(), _Reply(502), _Reply(429), _Reply(403)):
        _net(monkeypatch, post={'mempool.space': first, 'blockstream': _Reply(400, text='rejected')})
        assert send.broadcast('00ff') == ('unknown', '')            # one explorer may have taken it

    _net(monkeypatch, post={'mempool.space': TimeoutError(), 'blockstream': _Reply(400, text='txn-already-known')})
    assert send.broadcast('00ff') == ('sent', '')
    _net(monkeypatch, post={'mempool.space': ConnectionError(), 'blockstream': ConnectionError()})
    assert send.broadcast('00ff') == ('unknown', '')


def test_seen_needs_both_explorers_to_say_no(monkeypatch):
    _net(monkeypatch, get={'mempool.space': _Reply(404), 'blockstream': _Reply(200)})
    assert send.seen('ab' * 32) is True
    _net(monkeypatch, get={'mempool.space': _Reply(404), 'blockstream': _Reply(404)})
    assert send.seen('ab' * 32) is False
    for other in (TimeoutError(), _Reply(503)):
        _net(monkeypatch, get={'mempool.space': _Reply(404), 'blockstream': other})
        assert send.seen('ab' * 32) is None


# ── where it may go, and how little ──────────────────────────────────────

def test_addresses(world):
    for bad, why in (('bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr', 'taproot'),
                     ('tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx', 'testnet'),
                     ('1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb', 'not a valid Bitcoin address'),
                     ('garbage', 'not a valid Bitcoin address'), ('', 'required'),
                     (world.key.address, "wallet's own address")):
        text, ok = world.send(to=bad)
        assert not ok and why in text, bad
    assert not world.net.posted
    assert send.clean_address(f"  bitcoin:{world.dest}?amount=5 ") == world.dest
    assert send.clean_address('bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4')
    assert world.send(to=f"bitcoin:{world.dest}?amount=5")[1]
    assert _rows(world)[0]['sat'] == 100_000            # the amount asked for, not the link's


def test_too_small_to_send(world):
    text, ok = world.send('0.00000545')
    assert not ok and 'too small' in text


@pytest.mark.parametrize('coin,fee,outputs', [(101_200, 1200, 1),      # 522 of change: bit would sign an output nodes refuse
                                             (101_000, 1000, 1),      # 322: bit folds this one itself
                                             (101_300, 678, 2)])      # 622: real change, comes home
def test_change_too_small_for_the_network_goes_to_the_fee(world, coin, fee, outputs):
    world.key = _wallet(coin)
    text, ok = world.send('0.001')
    [row] = _rows(world)
    assert ok and row['fee'] == fee and f"Fee: {fee} satoshi" in text
    outs = [int.from_bytes(o.amount, 'little') for o in deserialize(row['hex']).TxOut]
    assert len(outs) == outputs and outs[0] == 100_000 and all(o >= send.DUST for o in outs)


def test_not_enough(world):
    world.key = _wallet(50_000)
    text, ok = world.send('0.001')
    assert not ok and 'Insufficient balance: the wallet has 0.00050000 BTC' in text
    world.key = _wallet(100_500)                        # enough for the payment, not for its fee
    text, ok = world.send('0.001')
    assert not ok and 'plus its fee' in text and not world.net.posted
    world.key = _wallet()
    assert 'Insufficient balance' in world.send('0.001')[0]


def test_an_explorer_error_while_reading_coins_sends_nothing(world):
    def down():
        raise ConnectionError("All APIs are unreachable.")
    world.key.get_unspents = down
    text, ok = world.send('0.001')
    assert not ok and 'could not be read from the network' in text and not _rows(world)


# ── the tools ────────────────────────────────────────────────────────────

@pytest.fixture
def tool(world, monkeypatch):
    monkeypatch.setattr(bitcoin_tool, '_get_wallet_key', lambda: (world.key, None))
    monkeypatch.setattr(bitcoin_tool, '_get_current_bitcoin_scope', lambda: 'main')
    return world


def test_send_tool_passes_the_users_limits(tool):
    out, ok = bitcoin_tool.execute('send_bitcoin', {'address': tool.dest, 'amount': '0.001'}, None, {'max_send_btc': '0.0005'})
    assert not ok and 'limit of 0.00050000 BTC per send' in out
    out, ok = bitcoin_tool.execute('send_bitcoin', {'address': tool.dest, 'amount': '0.001'}, None, {})
    assert ok and 'Transaction:' in out


def test_send_tool_keeps_its_receipt_and_is_a_write():
    [tool_def] = [t for t in bitcoin_tool.TOOLS if t['function']['name'] == 'send_bitcoin']
    assert tool_def['expires'] == 'never' and tool_def['writes'] is True and tool_def['is_local'] is False


def test_get_wallet_reads_once_and_says_the_limits(tool):
    reads = []
    coins = tool.key.get_unspents()
    tool.key.get_unspents = lambda: reads.append(1) or coins
    tool.key.get_balance = lambda *a: pytest.fail("a second network read")
    out, ok = bitcoin_tool.execute('get_wallet', {}, None, {'max_send_btc': '0.01', 'max_day_btc': '0'})
    assert ok and reads == [1]
    assert 'Balance: 0.02120000 BTC (2120000 sat)' in out and 'Coins (UTXOs): 3' in out
    assert '0.01000000 BTC per send, none per day' in out


@pytest.mark.parametrize('count', [10.5, None, 'ten', 0, 999, '3'])
def test_get_transactions_takes_any_count(tool, count):
    tool.key.get_transactions = lambda: [f"{i:064x}" for i in range(60)]
    tool.send('0.001')
    out, ok = bitcoin_tool.execute('get_transactions', {'count': count}, None)
    assert ok and 'Sent from here' in out and tool.dest in out
    assert 'in the order it gives them' in out


def test_explorer_down_is_said_plainly(tool):
    def down():
        raise ConnectionError("All APIs are unreachable.")
    tool.key.get_unspents = tool.key.get_transactions = down
    for name in ('get_wallet', 'get_transactions'):
        out, ok = bitcoin_tool.execute(name, {}, None)
        assert not ok and 'block explorers did not answer' in out


def test_a_crash_in_a_tool_never_repeats_its_message(tool, monkeypatch, caplog):
    secret = 'KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn'
    monkeypatch.setattr(bitcoin_tool, '_get_wallet', lambda **k: (_ for _ in ()).throw(ValueError(f"bad key {secret}")))
    with caplog.at_level('DEBUG'):
        out, ok = bitcoin_tool.execute('get_wallet', {}, None)
    assert not ok and secret not in out and secret not in caplog.text and 'ValueError' in out


def test_the_tool_says_which_kind_of_no_wallet(monkeypatch):
    from plugins.bitcoin import wallets
    monkeypatch.setitem(sys.modules, 'core.credentials_manager', SimpleNamespace(credentials=object()))
    monkeypatch.setattr(bitcoin_tool, '_get_current_bitcoin_scope', lambda: 'main')
    for state, words in (('missing', "no Bitcoin wallet named 'main'"), ('unreadable', 'The key is not lost'),
                         ('invalid', 'not valid')):
        monkeypatch.setattr(wallets, 'look', lambda c, s, state=state: {'state': state, 'key': None})
        key, err = bitcoin_tool._get_wallet_key()
        assert key is None and words in err
    monkeypatch.setattr(bitcoin_tool, '_get_current_bitcoin_scope', lambda: None)
    assert 'No Bitcoin wallet is picked for this chat' in bitcoin_tool._get_wallet_key()[1]
