# Bitcoin wallet tool — plugin tool
"""
Bitcoin wallet tool — AI can check balance, show a pay QR code, send BTC, and view transactions.
Uses the `bit` library for key management and blockchain interaction.
Wallet private keys (WIF) stored encrypted in credentials_manager.
"""

import logging
import time
import traceback

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '₿'
TOOL_CATEGORY = 'bitcoin'
AVAILABLE_FUNCTIONS = [
    'get_wallet',
    'bitcoin_receive',
    'send_bitcoin',
    'get_transactions',
]

TOOLS = [
    {
        "type": "function",
        "is_local": False,
        "function": {
            "name": "get_wallet",
            "description": "Your Bitcoin wallet's receive address, balance and send limits.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "bitcoin_receive",
            "description": "Show the user a QR code they can scan to send Bitcoin to your wallet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "string",
                        "description": "BTC to ask for, e.g. '0.001'. Leave out for any amount."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "writes": True,
        "is_local": False,
        "expires": "never",        # the transaction id is the only receipt: it never leaves her view
        "function": {
            "name": "send_bitcoin",
            "description": "Send Bitcoin from your wallet. It cannot be undone. Returns the transaction id and the fee paid. The user sets limits; a refusal says which one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "The address to pay, exactly as given to you."
                    },
                    "amount": {
                        "type": "string",
                        "description": "In BTC, e.g. '0.001'"
                    }
                },
                "required": ["address", "amount"]
            }
        }
    },
    {
        "type": "function",
        "is_local": False,
        "function": {
            "name": "get_transactions",
            "description": "Transaction ids for your wallet, and what you sent lately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Default 10, max 50"
                    }
                },
                "required": []
            }
        }
    },
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_current_bitcoin_scope():
    try:
        from core.chat.function_manager import scope_bitcoin
        return scope_bitcoin.get()
    except Exception:
        return None


def _get_wallet_key():
    """Get a bit.Key for the current scope. Returns (key, error_msg) tuple."""
    try:
        import bit  # noqa: F401
    except ImportError:
        return None, "Bitcoin library not installed. Run: pip install bit"

    from core.credentials_manager import credentials
    from plugins.bitcoin import wallets
    scope = _get_current_bitcoin_scope()
    if scope is None:
        return None, "No Bitcoin wallet is picked for this chat. The user picks one in the chat's sidebar."

    found = wallets.look(credentials, scope)
    if found['state'] == 'ok':
        return found['key'], None
    if found['state'] == 'missing':
        return None, f"There is no Bitcoin wallet named '{scope}'. The user makes one in Settings > Plugins > Bitcoin."
    if found['state'] == 'unreadable':
        return None, (f"The wallet '{scope}' cannot be used: {wallets.UNREADABLE}. The key is not lost. The user "
                      "restores it from its backup file in Settings > Plugins > Bitcoin. Do not make a new wallet over it.")
    return None, (f"The wallet key stored for scope '{scope}' is not valid. "
                  "Re-import it in Settings > Plugins > Bitcoin.")


def _chat():
    try:
        from core.chat.function_manager import tool_context
        return (tool_context.get() or {}).get('chat') or ''
    except Exception:
        return ''


def _explorer_error(what, e):
    logger.error(f"Bitcoin {what} failed: {type(e).__name__}: {e}")
    return (f"Could not {what}: the block explorers did not answer ({type(e).__name__}). "
            "Nothing changed. Try again in a minute."), False


# ─── Tool Implementations ────────────────────────────────────────────────────

def _get_wallet(settings=None):
    key, err = _get_wallet_key()
    if err:
        return err, False

    from plugins.bitcoin import send
    scope = _get_current_bitcoin_scope()
    try:
        coins = key.get_unspents()          # one fetch: the balance is the sum of the coins
    except Exception as e:
        return _explorer_error("check the wallet", e)
    balance = sum(u.amount for u in coins)
    lim = send.limits(settings)
    spent = send.spent_today(send.ledger(), scope)
    lines = [
        f"Wallet: {scope}",
        f"Address: {key.address}",
        f"Balance: {send.btc(balance)} BTC ({balance} sat), as the block explorer reports it",
        f"Coins (UTXOs): {len(coins)}",
        "Send limit: " + (f"{send.btc(lim['send'])} BTC per send" if lim['send'] else "none per send")
        + (f", {send.btc(lim['day'])} BTC a day" if lim['day'] else ", none per day")
        + f" (sent in the last 24 hours: {send.btc(spent)} BTC)",
    ]
    return '\n'.join(lines), True


def _bitcoin_receive(amount=None):
    """The address and its QR code. No network: the address comes from the key."""
    key, err = _get_wallet_key()
    if err:
        return err, False

    from plugins.bitcoin import qr
    try:
        uri = qr.pay_uri(key.address, amount)
    except ValueError as e:
        return str(e), False

    lines = [
        f"Wallet: {_get_current_bitcoin_scope()}",
        f"Address: {key.address}",
        f"Pay link: {uri}",
    ]
    try:
        picture = qr.png(uri)
    except qr.QRUnavailable as e:
        return '\n'.join(lines + [f"No QR code was shown: {e}"]), False

    from core import images
    lines.append("A QR code for this pay link was put in the chat for the user.")
    return images.result('\n'.join(lines), [picture], display_only=True), True


def _send_bitcoin(address, amount, settings=None):
    key, err = _get_wallet_key()
    if err:
        return err, False
    from plugins.bitcoin import send
    return send.send(key, _get_current_bitcoin_scope(), address, amount, settings=settings, chat=_chat())


def _get_transactions(count=10):
    key, err = _get_wallet_key()
    if err:
        return err, False

    try:
        count = min(max(1, int(count)), 50)
    except (TypeError, ValueError):
        count = 10
    from plugins.bitcoin import send
    scope = _get_current_bitcoin_scope()
    try:
        txs = key.get_transactions()
    except Exception as e:
        return _explorer_error("get the transactions", e)

    lines = [f"Wallet: {scope}", f"Address: {key.address}"]
    sent = [r for r in send.ledger() if r.get('scope') == scope][-count:]
    if sent:
        lines.append("Sent from here, newest last:")
        for r in sent:
            when = time.strftime('%Y-%m-%d %H:%M', time.localtime(r.get('ts', 0)))
            what = {'sent': 'sent', 'failed': 'NOT sent (refused)', 'unknown': 'outcome NOT known',
                    'signed': 'outcome NOT known'}.get(r.get('status'), r.get('status'))
            lines.append(f"  {when}  {send.btc(r.get('sat', 0))} BTC to {r.get('to')}  {what}  {r.get('txid')}")
    if not txs:
        lines.append("The block explorer lists no transactions for this address.")
    else:
        lines.append(f"Transaction ids from the block explorer ({min(count, len(txs))} of {len(txs)}, in the "
                     f"order it gives them, in and out together):")
        lines += [f"  {tx}" for tx in txs[:count]]
        lines.append(f"Amounts and dates: https://mempool.space/address/{key.address}")
    return '\n'.join(lines), True


# ─── Executor ────────────────────────────────────────────────────────────────

def execute(function_name, arguments, config, plugin_settings=None):
    arguments = arguments if isinstance(arguments, dict) else {}
    try:
        if function_name == "get_wallet":
            return _get_wallet(settings=plugin_settings)
        elif function_name == "bitcoin_receive":
            return _bitcoin_receive(amount=arguments.get('amount'))
        elif function_name == "send_bitcoin":
            return _send_bitcoin(
                address=arguments.get('address', ''),
                amount=arguments.get('amount', ''),
                settings=plugin_settings,
            )
        elif function_name == "get_transactions":
            return _get_transactions(count=arguments.get('count', 10))
        else:
            return f"Unknown function: {function_name}", False
    except Exception as e:
        # the type and the frames, never the message: an error from the key library can quote a key
        logger.error(f"Bitcoin tool error in {function_name}: {type(e).__name__}\n"
                     + ''.join(traceback.format_tb(e.__traceback__)))
        return f"Bitcoin tool error ({type(e).__name__}). Nothing was sent.", False
