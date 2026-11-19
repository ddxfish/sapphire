# The wallet store, read honestly and written carefully.
"""The keys live in the credentials manager, which answers '' both for a wallet
that was never made and for one whose key this machine cannot decrypt. The
second is recoverable (the encrypted key is still in the file) until something
is saved over it, so `look` tells them apart and `save` never writes over a
key without being told which key it is replacing.
"""
import logging
import re

logger = logging.getLogger(__name__)

NAME_OK = re.compile(r'^[a-z0-9_-]{1,64}$')
LABEL_MAX = 60
UNREADABLE = ("its key is stored but this machine cannot decrypt it (the machine's name or the user "
              "Sapphire runs as changed, or the credentials file came from another machine)")


def look(credentials, scope):
    """{state, label, key, address}. state: 'ok', 'missing' (no such wallet),
    'unreadable' (stored, cannot be decrypted here) or 'invalid' (decrypts to
    something that is not a key). key and address only when 'ok'."""
    stored = credentials.get_bitcoin_wallet(scope)
    out = {'state': 'ok', 'label': stored.get('label') or scope, 'key': None, 'address': ''}
    if not stored.get('wif'):
        listed = any(w.get('scope') == scope for w in credentials.list_bitcoin_wallets())
        out['state'] = 'unreadable' if listed else 'missing'
        return out
    try:
        from bit import Key
        out['key'] = Key(stored['wif'])
        out['address'] = out['key'].address
    except Exception as e:
        # never str(e): bit quotes the whole key in a checksum error
        logger.error(f"[BITCOIN] stored key for wallet '{scope}' is not valid ({type(e).__name__})")
        out['state'] = 'invalid'
    return out


def _changed(scope, action):
    try:
        from core.event_bus import publish, Events
        publish(Events.SCOPE_CHANGED, {"kind": "bitcoin", "action": action, "name": scope})
    except Exception as e:
        logger.warning(f"[BITCOIN] scope event not sent: {type(e).__name__}")


def save(credentials, scope, wif='', label='', generate=False, replace=''):
    """Make a wallet, rename one, or put a new key in one. (answer, status).

    A key already in the wallet is replaced only when `replace` names it: the
    address it has now, or 'unreadable' for a key that cannot be read. Without
    that the answer is a 409 carrying both addresses, for the page to ask with.
    """
    scope = str(scope or '')
    if not NAME_OK.match(scope) or scope == 'none':
        return {"error": "A wallet name is lowercase letters, numbers, - and _ (and not 'none')."}, 400
    wif, replace = str(wif or '').strip(), str(replace or '')
    now = look(credentials, scope)
    label = ' '.join(str(label or '').split())[:LABEL_MAX] or (now['label'] if now['state'] != 'missing' else scope)

    if generate:
        if now['state'] != 'missing':
            return {"error": f"A wallet named '{scope}' already exists. Generate never replaces a key."}, 409
        from bit import Key
        wif = Key().to_wif()

    if not wif:                                   # a rename
        if now['state'] == 'missing':
            return {"error": "A private key is required for a new wallet."}, 400
        if now['state'] != 'ok':
            return {"error": f"The wallet '{scope}' cannot be changed: {UNREADABLE}. "
                             "Restore it from its backup file."}, 409
        wif, address = now['key'].to_wif(), now['address']
    else:
        try:
            from bit import Key
            address = Key(wif).address
        except Exception:
            # no detail: bit's error quotes what was typed, and that may be most of a real key
            return {"error": "That is not a valid private key (WIF)."}, 400
        if now['state'] == 'ok' and address != now['address'] and replace != now['address']:
            return {"error": f"The wallet '{scope}' already has a key.", "needs_replace": True,
                    "state": 'ok', "old_address": now['address'], "new_address": address}, 409
        if now['state'] in ('unreadable', 'invalid') and replace != 'unreadable':
            return {"error": f"The wallet '{scope}' has a key that cannot be read.", "needs_replace": True,
                    "state": now['state'], "old_address": '', "new_address": address}, 409

    if not credentials.set_bitcoin_wallet(scope, wif, label):
        # the store keeps the new key in memory when the disk refuses it: put back what was there
        try:
            if now['state'] == 'ok':
                credentials.set_bitcoin_wallet(scope, now['key'].to_wif(), now['label'])
            elif now['state'] == 'missing':
                credentials.delete_bitcoin_wallet(scope)
        except Exception:
            pass
        return {"error": "The wallet could not be written to disk. Nothing was saved."}, 500
    after = look(credentials, scope)
    if after['state'] != 'ok' or after['address'] != address:
        return {"error": "The wallet was saved but cannot be read back. Check the log before using it."}, 500
    _changed(scope, 'saved')
    return {"success": True, "scope": scope, "address": address, "label": label,
            "created": now['state'] == 'missing'}, 200


def remove(credentials, scope):
    """Delete a wallet and let go of every chat, persona and task that named it:
    one left pointing at the name would spend from the next wallet given it."""
    if not credentials.delete_bitcoin_wallet(scope):
        return {"error": f"No wallet named '{scope}', or it could not be deleted."}, 404
    try:
        from core.chat.scope_cleanup import sweep_orphaned_scope_ref
        sweep_orphaned_scope_ref('bitcoin_scope', scope)
    except Exception as e:
        logger.warning(f"[BITCOIN] wallet '{scope}' deleted, its users not released: {type(e).__name__}")
    _changed(scope, 'deleted')
    return {"success": True}, 200
