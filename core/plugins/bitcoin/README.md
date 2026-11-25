# Bitcoin

Sapphire can check your Bitcoin balance, show a QR code to pay her wallet, view transactions, and send BTC — all through conversation. Uses the `bit` library for key management and blockchain interaction.

## Setup

1. Open Settings → Plugins → Bitcoin (via Plugins page gear icon)
2. Add your wallet's **WIF private key**
   - This is stored encrypted in Sapphire's credentials manager
   - Never leaves your machine

### Getting Your WIF Key

If you have a wallet that exports WIF format, use that. If you're starting fresh, most Bitcoin wallet software can generate or export a WIF key. It starts with `5`, `K`, or `L` for mainnet.

**Important:** This is your actual private key. Only use wallets you're comfortable having Sapphire access. Consider using a dedicated wallet with limited funds.

## Paying a Wallet (QR code)

A Bitcoin address is too long to type. Two ways to get a QR code a phone wallet can scan:

- **Settings → Plugins → Bitcoin → open a wallet.** The pay QR code is under the address. Type an amount to bake it into the code.
- **Ask Sapphire**: "Show me your pay QR" or "Show a QR for 0.001 BTC". The code appears in the chat.

The code holds `bitcoin:<address>` (with `?amount=` when you give one). It is drawn on your machine from the stored key, with no network.

QR codes need one small package, `segno`. If it is missing, the wallet page shows an **Install** button; everything else in the plugin works without it.

## Available Tools

| Tool | What it does |
|------|--------------|
| `bitcoin_wallet` | Show your wallet address, current BTC balance and send limits |
| `bitcoin_receive` | Show a QR code to pay the wallet, with an optional amount |
| `bitcoin_send` | Send BTC to an address, within your limits |
| `bitcoin_transactions` | What the wallet sent, and its transaction ids (up to 50) |

### Sending Bitcoin

The AI can send BTC when you ask:
- "Send 0.001 BTC to bc1q..."
- "Transfer 0.002 bitcoin to this address" (an amount over your limits is refused: the default is at most 0.005 BTC at once)

Amounts are specified in BTC (e.g., `"0.001"`) and are exact to the satoshi.

A send cannot be taken back, so it runs on rails. They are on the wallet list, under **What she may send**:

| Limit | Default | What it does |
|-------|---------|--------------|
| Most at once | 0.005 BTC | A bigger send is refused. 0 = no limit |
| Most per wallet in 24 hours | 0.02 BTC | Counts what left and what may have left. 0 = no limit |
| Highest network fee | 100 sat/byte | When the network asks more, nothing is sent |
| Send on turns no person started | off | Off: she sends only when you typed, spoke or used a device. Agents, scheduled tasks and messages over MCP are refused |

Also built in, with no switch:

- **The same payment is never sent twice by mistake.** The same amount to the same address is refused for 10 minutes. Regenerate, an edited message or a retry cannot pay again.
- **Every send is written down before it leaves.** The wallet's page lists what it sent, with the transaction id, and you get a notification for each send. That record is right even when the chat is not (a reply you stopped mid-send says "cancelled" in the chat).
- **"Failed" means nothing left.** When no block explorer answers the broadcast, she is told the outcome is not known, with the transaction id, and the wallet sends nothing more until the network shows the payment or 15 minutes pass without it.
- **The fee is looked up, limited and reported.** If it cannot be looked up, nothing is sent. Only the coins a payment needs are spent.

It can pay addresses that start with `1`, `3` or `bc1q`. Taproot addresses (`bc1p`) are not supported yet.

## Multi-Wallet

Multiple wallets are supported via scopes — one wallet per scope.

1. Add a wallet for each scope in Settings → Plugins → Bitcoin
2. Pick one per chat with the Bitcoin dropdown in the chat's sidebar. A chat, task or agent with no wallet picked has no wallet.

Putting a different key into a wallet that already has one asks first and shows both addresses. Generate never replaces a key. Deleting a wallet also lets go of every chat, persona and task that named it, so a new wallet with the same name does not inherit them.

### "Key cannot be read on this machine"

The stored key is encrypted with this machine's name and the user Sapphire runs as. If either changes (a renamed computer, a Docker container rebuilt without a fixed `hostname:`, a credentials file copied from another machine), the key is still in the file but cannot be decrypted. **Change the name back and restart Sapphire and the wallet returns.** Or restore from your backup: open the wallet and paste the backup's key, or use Import from File. Nothing is written over the unreadable key until you confirm.

## Example Commands

- "What's my Bitcoin balance?"
- "Show my wallet address"
- "Show me a QR code to send you 0.001 BTC"
- "Show my last 10 transactions"
- "Send 0.005 BTC to bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"

## Troubleshooting

- **Balance shows 0** — Verify the WIF key matches the wallet you expect. Check on blockstream.info
- **A send was refused** — She is told why, and it is one of: over a limit, the same payment inside 10 minutes, a turn no person started, the fee is over your limit or could not be looked up, not enough BTC for the amount plus the fee, or an earlier send whose outcome is not known yet
- **"May or may not have gone out"** — Open the transaction id on mempool.space. Do not ask her to send it again; the wallet holds until it is known
- **"No Bitcoin wallet is picked for this chat"** — Pick one in the chat's sidebar
- **Tools not available** — Add Bitcoin tools to your active toolset

## Reference for AI

Bitcoin wallet integration via `bit` library.

SETUP:
- Add WIF private key via plugin settings
- Keys stored encrypted in credentials_manager

AVAILABLE TOOLS:
- bitcoin_wallet() - address, balance, the user's send limits and what was sent in the last 24 hours
- bitcoin_receive(amount?) - show the user a pay QR code (bitcoin:<address>?amount=); local, no network; you get the address and pay link as text, the picture is for the user only
- bitcoin_send(address, amount) - send BTC (amount as string e.g. "0.001"); returns the transaction id and the fee paid; cannot be undone
- bitcoin_transactions(count?) - what this wallet sent (from the plugin's ledger, with outcome) and the explorer's transaction ids (1-50, default 10)

SENDING (plugins/bitcoin/send.py):
- Order of checks: person turn (send.lane) > address > exact amount > per-send limit > earlier unknown send > same payment in 10 min > 24 h limit > fee rate > build and check > ledger row > broadcast
- Limits are plugin settings owned by the wallet page: max_send_btc, max_day_btc (0 = none), max_fee_rate, machine_sends
- Ledger: plugin state key `sends` (user/plugin_state/bitcoin.json), row written with status `signed` BEFORE the broadcast, then `sent` | `failed` | `unknown`
- A refusal starts "Nothing was sent" and names the reason. "NOT CONFIRMED EITHER WAY" means the outcome is not known: never retry it, tell the user the transaction id
- Fee lookup and broadcast go through core.net to mempool.space and blockstream.info; bit only builds and signs (combine=False)

MULTI-WALLET:
- scope_bitcoin ContextVar for per-scope wallet routing; its fallback is None (no wallet), never a real wallet name
- One WIF key per scope in credentials. plugins/bitcoin/wallets.py: look() tells missing / unreadable / invalid apart; save() never replaces a key without `replace` naming it
- Page writes go through /api/plugin/bitcoin/wallet/{scope} (PUT, DELETE), check/{scope}, sends/{scope}

OUTPUT:
- Balances shown in BTC and satoshis
- Transactions link to mempool.space

TROUBLESHOOTING:
- Wrong balance: verify WIF key matches expected wallet
- "No QR code was shown": the segno package is missing; the wallet page in Settings → Plugins → Bitcoin has the Install button
- "cannot be used: its key is stored but this machine cannot decrypt it": the hostname or OS user changed; the key is not lost, never make a new wallet over it
- The tool names before 1.2.0 (get_wallet, send_bitcoin, get_transactions) still work as hidden aliases, so a saved toolset that lists them keeps working.
