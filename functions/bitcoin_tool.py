# functions/bitcoin_tool.py
"""
Bitcoin wallet tool — AI can check balance, receive address, send BTC, and view transactions.
Uses the `bit` library for key management and blockchain interaction.
Wallet private keys (WIF) stored encrypted in credentials_manager.
"""

import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '₿'

AVAILABLE_FUNCTIONS = [
    'get_wallet',
    'send_bitcoin',
    'get_transactions',
]

TOOLS = [
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_wallet",
            "description": "Get the current Bitcoin wallet address and balance. Shows the receive address for incoming payments.",
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
            "name": "send_bitcoin",
            "description": "Send Bitcoin to an address. Amount in BTC (e.g. 0.001). Returns transaction ID on success.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Destination Bitcoin address"
                    },
                    "amount": {
                        "type": "string",
                        "description": "Amount to send in BTC (e.g. '0.001')"
                    }
                },
                "required": ["address", "amount"]
            }
        }
    },
    {
        "type": "function",
        "is_local": True,
        "function": {
            "name": "get_transactions",
            "description": "Get recent Bitcoin transactions for the current wallet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of recent transactions to fetch (default 10, max 50)"
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
        from core.chat.function_manager import FunctionManager
        return FunctionManager._current_bitcoin_scope
    except Exception:
        return None


def _get_wallet_key():
    """Get a bit.Key for the current scope. Returns (key, error_msg) tuple."""
    try:
        from bit import Key
    except ImportError:
        return None, "Bitcoin library not installed. Run: pip install bit"

    from core.credentials_manager import credentials
    scope = _get_current_bitcoin_scope()
    if scope is None:
        return None, "Bitcoin wallet is disabled for this chat."

    wallet = credentials.get_bitcoin_wallet(scope)
    if not wallet['wif']:
        return None, f"No Bitcoin wallet configured for scope '{scope}'. Set one up in Settings → Plugins → Bitcoin."

    try:
        key = Key(wallet['wif'])
        return key, None
    except Exception as e:
        return None, f"Invalid wallet key for scope '{scope}': {e}"


def _satoshi_to_btc(satoshi):
    """Convert satoshi (int) to BTC string."""
    return f"{int(satoshi) / 1e8:.8f}"


# ─── Tool Implementations ────────────────────────────────────────────────────

def _get_wallet():
    key, err = _get_wallet_key()
    if err:
        return err, False

    scope = _get_current_bitcoin_scope()
    try:
        balance_sat = key.get_balance()
        balance_btc = _satoshi_to_btc(balance_sat)
        unspent = key.get_unspents()
        utxo_count = len(unspent)

        lines = [
            f"Wallet: {scope}",
            f"Address: {key.address}",
            f"Balance: {balance_btc} BTC ({balance_sat} sat)",
            f"UTXOs: {utxo_count}",
        ]
        return '\n'.join(lines), True
    except Exception as e:
        logger.error(f"Failed to check wallet: {e}", exc_info=True)
        return f"Failed to check wallet balance: {e}", False


def _send_bitcoin(address, amount):
    key, err = _get_wallet_key()
    if err:
        return err, False

    if not address or not amount:
        return "Both address and amount are required.", False

    try:
        amount_btc = float(amount)
        if amount_btc <= 0:
            return "Amount must be positive.", False
    except ValueError:
        return f"Invalid amount: {amount}. Use decimal BTC (e.g. '0.001').", False

    # Convert to satoshi for the send
    amount_sat = int(amount_btc * 1e8)
    scope = _get_current_bitcoin_scope()

    try:
        # Check balance first
        balance_sat = int(key.get_balance())
        if amount_sat > balance_sat:
            return f"Insufficient balance. Have {_satoshi_to_btc(balance_sat)} BTC, tried to send {amount} BTC.", False

        tx_hash = key.send([(address, amount_sat, 'satoshi')])
        logger.info(f"Bitcoin sent: {amount} BTC to {address} (scope={scope}, tx={tx_hash})")
        return f"Sent {amount} BTC to {address}\nTransaction: {tx_hash}", True

    except Exception as e:
        logger.error(f"Bitcoin send error: {e}", exc_info=True)
        return f"Failed to send Bitcoin: {e}", False


def _get_transactions(count=10):
    key, err = _get_wallet_key()
    if err:
        return err, False

    count = min(max(1, count), 50)
    scope = _get_current_bitcoin_scope()

    try:
        txs = key.get_transactions()
        if not txs:
            return f"Wallet: {scope}\nAddress: {key.address}\nNo transactions found.", True

        # bit returns list of tx hashes
        recent = txs[:count]
        lines = [
            f"Wallet: {scope}",
            f"Address: {key.address}",
            f"Recent transactions ({len(recent)} of {len(txs)}):",
        ]
        for i, tx_hash in enumerate(recent, 1):
            lines.append(f"  [{i}] {tx_hash}")
        lines.append(f"\nView on blockchain: https://blockstream.info/address/{key.address}")
        return '\n'.join(lines), True

    except Exception as e:
        logger.error(f"Failed to get transactions: {e}", exc_info=True)
        return f"Failed to get transactions: {e}", False


# ─── Executor ────────────────────────────────────────────────────────────────

def execute(function_name, arguments, config):
    try:
        if function_name == "get_wallet":
            return _get_wallet()
        elif function_name == "send_bitcoin":
            return _send_bitcoin(
                address=arguments.get('address', ''),
                amount=arguments.get('amount', ''),
            )
        elif function_name == "get_transactions":
            return _get_transactions(count=arguments.get('count', 10))
        else:
            return f"Unknown function: {function_name}", False
    except Exception as e:
        logger.error(f"Bitcoin tool error: {e}", exc_info=True)
        return f"Bitcoin tool error: {e}", False
