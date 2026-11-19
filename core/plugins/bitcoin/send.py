# Sending Bitcoin: the rails, the ledger, the fee and the broadcast.
"""A send cannot be taken back, so this path is built to never pay twice and
never lose track of a payment:

  - the amount is exact decimal, never a float
  - a turn no person started is refused unless the user allowed it
  - a per-send limit and a 24 hour limit, both the user's
  - the fee rate is looked up here (with a timeout), limited, and reported
  - the transaction is signed and written to the ledger BEFORE it is broadcast,
    so its id survives a failed broadcast, a stopped reply or a crash
  - a broadcast whose outcome is not known holds the wallet until the network
    answers; the same payment is not sent again inside REPEAT_S

bit builds and signs; the fee lookup and the broadcast are ours, through
core.net, against two public block explorers that speak the same API.
"""
import logging
import threading
import time
import uuid
from decimal import Decimal

logger = logging.getLogger(__name__)

ESPLORA = ('https://mempool.space/api', 'https://blockstream.info/api')
SAT = Decimal(100_000_000)
DUST = 546                # satoshi: below this, nodes refuse the payment
REPEAT_S = 600            # the same payment (wallet, address, amount) is refused this long
UNSURE_HOLD_S = 900       # a send nobody has seen after this long never went out
DAY_S = 86400
KEEP = 300                # ledger rows kept
DEFAULTS = {'max_send_btc': '0.005', 'max_day_btc': '0.02', 'max_fee_rate': 100, 'machine_sends': False}

_lock = threading.Lock()  # one send at a time: the checks and the ledger row are one step


class Refused(Exception):
    """Nothing was sent, and the message says why. Fit to show."""


def btc(sat):
    return f"{Decimal(int(sat)) / SAT:.8f}"


# ── the user's limits ────────────────────────────────────────────────────

def limits(settings):
    """{send, day (satoshi; 0 = no limit), fee_rate, machine_sends}. A value
    that cannot be read is the default, never 'no limit'."""
    settings = settings or {}

    def sat(key):
        for raw in (settings.get(key), DEFAULTS[key]):
            try:
                value = Decimal(str(raw).strip())
                if value.is_finite() and value >= 0:
                    return int(value * SAT)
            except Exception:
                pass

    try:
        rate = settings.get('max_fee_rate')
        if isinstance(rate, bool) or int(rate) < 1:
            raise ValueError
        rate = int(rate)
    except Exception:
        rate = DEFAULTS['max_fee_rate']
    return {'send': sat('max_send_btc'), 'day': sat('max_day_btc'), 'fee_rate': rate,
            'machine_sends': settings.get('machine_sends') is True}


# ── who started this turn ────────────────────────────────────────────────

def lane():
    """'person' when a person started the turn this tool call belongs to (they
    typed, spoke, or used a device). Otherwise what did: a task or an agent, a
    message over MCP, another machine lane. What cannot be told is not a person."""
    try:
        from core.chat.function_manager import tool_context
        ctx = tool_context.get() or {}
        if ctx.get('channel'):
            return 'a task or an agent'
        from core import mcp_server
        if mcp_server.answering.get():
            return 'a message over MCP'
        chat = ctx.get('chat')
        if not chat:
            return 'an unknown lane'
        from core import idle
        from core.api_fastapi import get_system
        writing = [s for s in get_system().llm_chat.streams_for_chat(chat) if not s.llm_done]
        if len(writing) != 1:
            return 'an unknown lane'
        source = getattr(writing[0], 'turn_source', None)     # set by core.cadence for a turn nobody typed
        return 'person' if idle.is_person(source) else f"a machine turn ({str(source).split(':', 1)[0]})"
    except Exception as e:
        logger.warning(f"[BITCOIN] could not tell who started the turn ({type(e).__name__})")
        return 'an unknown lane'


# ── the ledger ───────────────────────────────────────────────────────────

def _state():
    from core.plugin_loader import plugin_loader
    return plugin_loader.get_plugin_state('bitcoin')


def ledger(state=None):
    rows = (state or _state()).get('sends', [])
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _write(state, rows):
    state.save('sends', rows[-KEEP:])


def _set(state, row_id, **changes):
    rows = ledger(state)
    for r in rows:
        if r.get('id') == row_id:
            r.update(changes)
    _write(state, rows)


def spent_today(rows, scope, now=None):
    """Satoshi that left, or may have left, this wallet in the last 24 hours."""
    now = now or time.time()
    return sum(int(r.get('sat', 0)) for r in rows
               if r.get('scope') == scope and r.get('status') != 'failed' and now - r.get('ts', 0) < DAY_S)


# ── the network ──────────────────────────────────────────────────────────

def fee_rate():
    """Satoshi per byte for a quick confirmation, or None when no explorer says."""
    from core import net
    try:
        r = net.get(f"{ESPLORA[0]}/v1/fees/recommended", timeout=8)
        if r.status_code == 200:
            return max(1, int(r.json()['fastestFee']))
    except Exception as e:
        logger.warning(f"[BITCOIN] fee lookup failed at the first explorer ({type(e).__name__})")
    try:
        r = net.get(f"{ESPLORA[1]}/fee-estimates", timeout=8)
        if r.status_code == 200:
            return max(1, int(-(-float(r.json()['2']) // 1)))       # rounded up
    except Exception as e:
        logger.warning(f"[BITCOIN] fee lookup failed at the second explorer ({type(e).__name__})")
    return None


def broadcast(tx_hex):
    """('sent', '') when a node took it; ('failed', why) when every explorer
    refused it; ('unknown', '') when any of them did not answer."""
    from core import net
    refused, unsure = [], False
    for base in ESPLORA:
        try:
            r = net.post(f"{base}/tx", data=tx_hex, timeout=20)
        except Exception as e:
            logger.warning(f"[BITCOIN] broadcast got no answer from an explorer ({type(e).__name__})")
            unsure = True
            continue
        why = (r.text or '').strip()[:300]
        if r.status_code == 200 or 'already' in why.lower():       # already known = it is out
            return 'sent', ''
        if r.status_code in (400, 422):
            refused.append(why)
        else:
            unsure = True
    if unsure or not refused:
        return 'unknown', ''
    return 'failed', refused[0]


def seen(txid):
    """True when an explorer knows the transaction, False when both say they
    do not, None when that cannot be told right now."""
    from core import net
    unknown = 0
    for base in ESPLORA:
        try:
            r = net.get(f"{base}/tx/{txid}", timeout=8)
        except Exception:
            continue
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            unknown += 1
    return False if unknown == len(ESPLORA) else None


def _settle(state, scope, now):
    """Look again at this wallet's sends whose outcome is not known. Returns
    the first one still open, or None."""
    for r in ledger(state):
        if r.get('scope') != scope or r.get('status') not in ('signed', 'unknown'):
            continue
        found = seen(r.get('txid', ''))
        if found:
            _set(state, r['id'], status='sent')
        elif found is False and now - r.get('ts', 0) > UNSURE_HOLD_S:
            _set(state, r['id'], status='failed', why='never reached the network')
        else:
            return r
    return None


def _tell_user(row, title, level='info'):
    """A notification: the record a person sees even when the chat lost it
    (a reply stopped mid-send says 'cancelled' in the chat)."""
    try:
        from core import notify
        notify.notify(title, level=level, source='bitcoin', key=f"btc-send-{row['id']}",
                      body=f"{btc(row['sat'])} BTC from wallet '{row['scope']}' to {row['to']}. "
                           f"Transaction {row['txid']}")
    except Exception as e:
        logger.warning(f"[BITCOIN] notification not posted ({type(e).__name__})")


# ── the send ─────────────────────────────────────────────────────────────

def clean_address(address):
    """The address out of what was given (a pasted `bitcoin:` link is fine).
    Raises Refused when this wallet cannot pay it."""
    from bit.format import get_version
    from bit.transaction import address_to_scriptpubkey
    text = str(address or '').strip()
    if text.lower().startswith('bitcoin:'):
        text = text[8:].split('?', 1)[0]
    if not text:
        raise Refused("An address is required.")
    if text.lower().startswith('bc1p'):
        raise Refused("This wallet cannot pay taproot addresses (they start with bc1p) yet.")
    try:
        version = get_version(text)
        address_to_scriptpubkey(text)
    except Exception:
        raise Refused(f"'{text}' is not a valid Bitcoin address (the checksum does not match).")
    if version != 'main':
        raise Refused(f"'{text}' is a testnet address. This wallet holds real Bitcoin.")
    return text


def satoshi(amount):
    """Exact satoshi from decimal BTC. Raises Refused."""
    from plugins.bitcoin import qr
    if isinstance(amount, bool):
        raise Refused("Amount must be decimal BTC (e.g. '0.001').")
    try:
        text = qr.clean_amount(amount)
    except ValueError as e:
        raise Refused(str(e))
    if text is None:
        raise Refused("An amount is required.")
    return int(Decimal(text) * SAT)


def _build(key, address, sat, rate):
    """Sign a payment. (tx_hex, txid, fee paid). Raises Refused. No network
    but the coin list."""
    from bit.exceptions import InsufficientFunds
    from bit.transaction import address_to_scriptpubkey, calc_txid, deserialize
    coins = list(key.get_unspents())
    have = sum(u.amount for u in coins)
    if not coins or have < sat:
        raise Refused(f"Insufficient balance: the wallet has {btc(have)} BTC, the payment is {btc(sat)} BTC.")
    own = {address_to_scriptpubkey(a) for a in (key.address, key.segwit_address) if a}

    def checked(tx_hex, spent):
        """(outputs, fee) of what was signed, or Refused: it is checked before it can leave."""
        outs = [(int.from_bytes(o.amount, 'little'), o.script_pubkey) for o in deserialize(tx_hex).TxOut]
        fee = sum(u.amount for u in spent) - sum(a for a, _ in outs)
        size = len(tx_hex) // 2
        if (not outs or outs[0] != (sat, address_to_scriptpubkey(address)) or len(outs) > 2
                or any(script not in own for _, script in outs[1:]) or not 0 < fee <= 2 * rate * size + DUST):
            logger.error(f"[BITCOIN] built transaction failed its check (outs={len(outs)}, fee={fee}, size={size})")
            raise Refused("Nothing was sent: the transaction that was built did not match the payment asked for.")
        return outs, fee

    try:
        # combine=False: spend the coins this payment needs, not every coin in the wallet.
        # bit leaves in `coins` the ones it picked.
        tx_hex = key.create_transaction([(address, sat, 'satoshi')], fee=rate, combine=False, unspents=coins)
        outs, fee = checked(tx_hex, coins)
        if len(outs) == 2 and outs[1][0] < DUST:
            # change too small for the network to carry: it goes to the fee, as it does in any wallet
            tx_hex = key.create_transaction([(address, sat, 'satoshi')], fee=sum(u.amount for u in coins) - sat,
                                            absolute_fee=True, combine=False, unspents=coins)
            outs, fee = checked(tx_hex, coins)
            if len(outs) != 1:
                raise Refused("Nothing was sent: the change was too small for the network and could not be folded into the fee.")
    except InsufficientFunds:
        raise Refused(f"Insufficient balance for the payment plus its fee: the wallet has {btc(have)} BTC, "
                      f"the payment is {btc(sat)} BTC and the fee rate is {rate} sat/byte.")
    return tx_hex, calc_txid(tx_hex), fee


def send(key, scope, address, amount, settings=None, state=None, chat=''):
    """(text, ok) for the model. Every refusal says what to do next."""
    try:
        with _lock:
            return _send(key, scope, address, amount, limits(settings), state or _state(), chat)
    except Refused as e:
        return str(e), False


def _send(key, scope, address, amount, lim, state, chat):
    now = time.time()
    where = lane()
    if where != 'person' and not lim['machine_sends']:
        raise Refused(f"Nothing was sent: sending is off for turns no person started, and this one came from "
                      f"{where}. The user can allow it in Settings > Plugins > Bitcoin.")
    address = clean_address(address)
    sat = satoshi(amount)
    if sat < DUST:
        raise Refused(f"{btc(sat)} BTC is too small to send: the network refuses payments under {DUST} satoshi.")
    if address in (key.address, key.segwit_address):
        raise Refused("That is this wallet's own address.")
    if lim['send'] and sat > lim['send']:
        raise Refused(f"Nothing was sent: {btc(sat)} BTC is over the user's limit of {btc(lim['send'])} BTC per send. "
                      "Only the user can raise it, in Settings > Plugins > Bitcoin.")

    open_row = _settle(state, scope, now)
    if open_row:
        raise Refused(f"Nothing was sent: an earlier send from this wallet ({btc(open_row['sat'])} BTC to "
                      f"{open_row['to']}, transaction {open_row['txid']}) may or may not have gone out. The wallet "
                      f"sends nothing more until the network shows it, or {UNSURE_HOLD_S // 60} minutes pass "
                      f"without it. Check https://mempool.space/tx/{open_row['txid']}")
    rows = ledger(state)
    for r in reversed(rows):
        if (r.get('scope'), r.get('to'), r.get('sat'), r.get('status')) == (scope, address, sat, 'sent') \
                and now - r.get('ts', 0) < REPEAT_S:
            raise Refused(f"Nothing was sent: this exact payment ({btc(sat)} BTC to {address}) already went out "
                          f"{int((now - r['ts']) // 60)} minutes ago, transaction {r['txid']}. The same payment is "
                          f"refused for {REPEAT_S // 60} minutes so nothing is paid twice by mistake.")
    spent = spent_today(rows, scope, now)
    if lim['day'] and spent + sat > lim['day']:
        raise Refused(f"Nothing was sent: this wallet has sent {btc(spent)} BTC in the last 24 hours and the user's "
                      f"limit is {btc(lim['day'])} BTC a day. Only the user can raise it, in Settings > Plugins > Bitcoin.")

    rate = fee_rate()
    if rate is None:
        raise Refused("Nothing was sent: the network fee could not be looked up right now. Try again in a minute.")
    if rate > lim['fee_rate']:
        raise Refused(f"Nothing was sent: the network fee is {rate} sat/byte right now, over the user's limit of "
                      f"{lim['fee_rate']}. Try later, or the user can raise the limit in Settings > Plugins > Bitcoin.")
    try:
        tx_hex, txid, fee = _build(key, address, sat, rate)
    except Refused:
        raise
    except Exception as e:
        logger.error(f"[BITCOIN] could not build the transaction ({type(e).__name__})")
        raise Refused(f"Nothing was sent: the wallet's coins could not be read from the network ({type(e).__name__}). "
                      "Try again in a minute.")

    row = {'id': uuid.uuid4().hex[:12], 'ts': now, 'scope': scope, 'to': address, 'sat': sat, 'fee': fee,
           'rate': rate, 'txid': txid, 'hex': tx_hex, 'status': 'signed', 'chat': chat or '', 'lane': where}
    try:
        _write(state, rows + [row])           # on disk before it can leave
    except Exception as e:
        logger.error(f"[BITCOIN] ledger not written, send abandoned ({type(e).__name__})")
        raise Refused("Nothing was sent: the send could not be written to the ledger first.")

    status, why = broadcast(tx_hex)
    if status == 'unknown' and seen(txid):
        status = 'sent'
    _set(state, row['id'], status=status, **({'why': why} if why else {}))
    logger.info(f"[BITCOIN] send {status}: {sat} sat from '{scope}', fee {fee}, tx {txid}")

    if status == 'failed':
        raise Refused(f"Nothing was sent: the network refused the transaction ({why}). It is safe to try again.")
    if status == 'unknown':
        _tell_user(row, "A Bitcoin send may or may not have gone out", 'warning')
        return (f"NOT CONFIRMED EITHER WAY: {btc(sat)} BTC to {address} was signed, but no block explorer answered "
                f"the broadcast, so it may or may not have gone out.\nTransaction: {txid}\n"
                f"Do not send it again. Tell the user, and check https://mempool.space/tx/{txid} in a few minutes. "
                f"The wallet sends nothing more until this is known."), False
    _tell_user(row, f"Sent {btc(sat)} BTC")
    return (f"Sent {btc(sat)} BTC to {address}\nFee: {fee} satoshi ({rate} sat/byte)\n"
            f"Transaction: {txid}\nWallet: {scope}"), True
