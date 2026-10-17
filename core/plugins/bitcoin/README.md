# Bitcoin

Sapphire can check your Bitcoin balance, view transactions, and send BTC — all through conversation. Uses the `bit` library for key management and blockchain interaction.

## Setup

1. Open Settings → Plugins → Bitcoin (via Plugins page gear icon)
2. Add your wallet's **WIF private key**
   - This is stored encrypted in Sapphire's credentials manager
   - Never leaves your machine

### Getting Your WIF Key

If you have a wallet that exports WIF format, use that. If you're starting fresh, most Bitcoin wallet software can generate or export a WIF key. It starts with `5`, `K`, or `L` for mainnet.

**Important:** This is your actual private key. Only use wallets you're comfortable having Sapphire access. Consider using a dedicated wallet with limited funds.

## Available Tools

| Tool | What it does |
|------|--------------|
| `get_wallet` | Show your wallet address and current BTC balance |
| `send_bitcoin` | Send BTC to an address |
| `get_transactions` | View recent transactions (up to 50) |

### Sending Bitcoin

The AI can send BTC when you ask:
- "Send 0.001 BTC to bc1q..."
- "Transfer half a bitcoin to this address"

Amounts are specified in BTC (e.g., `"0.001"`).

## Multi-Wallet

Multiple wallets are supported via scopes — one wallet per scope.

1. Add wallet keys for each scope via credentials manager
2. Switch using the Bitcoin scope dropdown in Chat Settings

## Example Commands

- "What's my Bitcoin balance?"
- "Show my wallet address"
- "Show my last 10 transactions"
- "Send 0.005 BTC to bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"

## Troubleshooting

- **Balance shows 0** — Verify the WIF key matches the wallet you expect. Check on blockstream.info
- **Send failed** — Check you have enough BTC to cover amount + network fee
- **Tools not available** — Add Bitcoin tools to your active toolset

## Reference for AI

Bitcoin wallet integration via `bit` library.

SETUP:
- Add WIF private key via plugin settings
- Keys stored encrypted in credentials_manager

AVAILABLE TOOLS:
- get_wallet() - show address and BTC balance
- send_bitcoin(address, amount) - send BTC (amount as string e.g. "0.001")
- get_transactions(count?) - recent transactions (1-50, default 10)

MULTI-WALLET:
- scope_bitcoin ContextVar for per-scope wallet routing
- One WIF key per scope in credentials

OUTPUT:
- Balances shown in BTC and satoshis
- Transactions link to blockstream.info

TROUBLESHOOTING:
- Wrong balance: verify WIF key matches expected wallet
- Send failed: check balance covers amount + fee
