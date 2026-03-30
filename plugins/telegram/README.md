# Telegram Plugin

Connects Sapphire to Telegram with two account types: **Bot** and **Client**.

## Bot Accounts (Recommended)

Bot accounts are safe, simple, and purpose-built for automation. No ban risk.

### Setup

1. Open Telegram and message `@BotFather`
2. Send `/newbot`, choose a display name, choose a username (must end in `bot`)
3. BotFather gives you a token like `123456:ABC-DEF1234ghIkl-zyx57W2v`
4. In Sapphire, go to **Settings > Telegram**
5. Make sure **API ID** and **API Hash** are set (from https://my.telegram.org — create an app if you haven't; the URL/platform/description fields don't matter, use `https://localhost` or leave blank)
6. Click **+ Add Bot**, paste your token, give it a name, click Connect
7. Create a daemon task in **Schedule** to auto-reply to messages

### How users reach your bot

- Search `@YourBotName` in Telegram
- Share the link `t.me/YourBotName`
- Add the bot to a group

Users must tap **Start** before the bot can message them. After that, the bot can respond freely.

### Bot limitations

- Cannot initiate conversations (user must /start first)
- In groups, only sees commands and replies to it (unless made admin with privacy mode off)
- No access to message history before it joined

## Client Accounts (Power Mode)

Client accounts log in as a real Telegram user. Full access to everything a human can do.

### Setup

1. Go to https://my.telegram.org and get your **API ID** and **API Hash** (create an app if you haven't; the URL/platform/description fields don't matter, use `https://localhost` or leave blank)
2. In Sapphire, go to **Settings > Telegram** and enter them
3. Click **+ Add Client**, enter a name and phone number
4. Enter the verification code Telegram sends you
5. If you have 2FA enabled, enter your password
6. Create a daemon task in **Schedule** to auto-reply

### Client advantages

- Can message anyone first (no /start required)
- Sees all messages in groups
- Full chat history access
- 2GB file transfers

### Client risks

- Telegram can ban automated accounts that message too aggressively
- Phone number is tied to the account — if banned, the number is burned
- Technically a gray area in Telegram's ToS

## Using Both

You can run bot and client accounts simultaneously. Each account appears in the sidebar scope dropdown with an icon:

- `[robot]` Bot accounts
- `[phone]` Client accounts

Each account can have its own daemon task with different filters and prompts. Use the bot for public interactions and the client for private power-user workflows.

## Tools

All tools work identically for both account types:

| Tool | Description |
|------|-------------|
| `telegram_send` | Send a message to a chat by ID or @username |
| `telegram_get_chats` | List recent chats with unread counts and previews |
| `telegram_read_messages` | Read recent messages from a specific chat |

## Daemon Events

The plugin emits `telegram_message` events when messages arrive. Create tasks in Schedule with filters:

- **Account**: Which account to listen on
- **Chat ID**: Filter to specific chats
- **Username**: Filter by sender
- **Chat Type**: private, group, supergroup, channel
