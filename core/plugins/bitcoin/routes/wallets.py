"""The settings page's doors onto the wallet store and the send ledger.

  PUT    /api/plugin/bitcoin/wallet/{scope}   make, rename, or replace a key (plugins.bitcoin.wallets.save)
  DELETE /api/plugin/bitcoin/wallet/{scope}   delete, and release everything that named it
  POST   /api/plugin/bitcoin/check/{scope}    balance, off the event loop
  GET    /api/plugin/bitcoin/sends/{scope}    what this wallet sent

No answer here carries a private key, and no error repeats what was typed.
"""
import logging

logger = logging.getLogger(__name__)


def save(scope, body=None, credentials=None, **_):
    from plugins.bitcoin import wallets
    body = body or {}
    return wallets.save(credentials, scope, wif=body.get('wif'), label=body.get('label'),
                        generate=body.get('generate') is True, replace=body.get('replace'))


def delete(scope, credentials=None, **_):
    from plugins.bitcoin import wallets
    return wallets.remove(credentials, scope)


def check(scope, credentials=None, **_):
    from plugins.bitcoin import send, wallets
    found = wallets.look(credentials, scope)
    if found['state'] != 'ok':
        return {"error": f"The wallet '{scope}' has no key this machine can read."}, 409
    try:
        coins = found['key'].get_unspents()
    except Exception as e:
        logger.warning(f"[BITCOIN] balance check for '{scope}' failed ({type(e).__name__})")
        return {"error": f"The block explorers did not answer ({type(e).__name__}). Try again in a minute."}, 502
    sat = sum(u.amount for u in coins)
    return {"scope": scope, "address": found['address'], "balance_sat": sat,
            "balance_btc": send.btc(sat), "coins": len(coins)}


def sends(scope, **_):
    from plugins.bitcoin import send
    rows = [r for r in send.ledger() if r.get('scope') == scope][-20:]
    return {"scope": scope, "sends": [
        {"ts": r.get('ts'), "to": r.get('to'), "btc": send.btc(r.get('sat', 0)), "fee": r.get('fee'),
         "txid": r.get('txid'), "status": r.get('status'), "why": r.get('why', ''), "chat": r.get('chat', '')}
        for r in reversed(rows)]}
