# Discord

Connect Sapphire to your Discord server. She can read messages, reply in channels, and react to mentions or keywords automatically via daemons.

## Setup

1. **Create a Discord Bot:**
   - Go to [Discord Developer Portal](https://discord.com/developers/applications)
   - Click "New Application", give it a name
   - Go to **Bot** tab → click "Add Bot"
   - Under **Privileged Gateway Intents**, enable **Message Content Intent**
   - Copy the bot token

2. **Invite the Bot to Your Server:**
   - Go to **OAuth2 → URL Generator**
   - Select scopes: `bot`
   - Select permissions: `Send Messages`, `Read Message History`, `View Channels`
   - Open the generated URL and add the bot to your server

3. **Configure in Sapphire:**
   - Open Settings → Plugins → Discord
   - Click "Add Account"
   - Paste your bot token
   - Test the connection

## Available Tools

The AI can use these when Discord tools are in the active toolset:

| Tool | What it does |
|------|--------------|
| `discord_get_servers` | List servers the bot is in and their text channels |
| `discord_read_messages` | Read recent messages from a channel (up to 50) |
| `discord_send_message` | Send a message to a channel (max 2000 chars) |

If you don't specify a channel, the tools use the current scope's default.

## Multi-Account

You can connect multiple Discord bots (one per server, or different bots for different purposes). Each gets its own scope in the sidebar dropdown.

1. Add accounts in Settings → Plugins → Discord
2. Switch between them using the Discord scope dropdown in Chat Settings

## Daemon (Auto-React to Messages)

The real power is the daemon — Sapphire listens for Discord messages in the background and can respond automatically.

### Quick Setup

1. Go to **Schedule** → **+ New Task** → choose **Daemon**
2. Set source to **Discord Message**
3. Configure filters (see below)
4. Enable **Auto-reply in channel** if you want the AI to respond in Discord
5. Set a prompt, toolset, and optionally a named chat

### Filters

All filters are AND'd — every condition must match.

| Filter | What it matches |
|--------|----------------|
| `mentioned` | `"true"` or `"false"` — was the bot @mentioned |
| `guild_name` | Server name |
| `channel_name` | Channel name |
| `username` | Message author |
| `content_contains` | Substring in message text |
| `channel_name_not` | Exclude a channel |
| `guild_name_not` | Exclude a server |
| `username_not` | Exclude a user (handy for ignoring other bots) |

### Example: Respond to Mentions

```json
{"mentioned": "true", "channel_name_not": "rules"}
```
Auto-reply on, prompt set to a helpful assistant. Bot replies when @mentioned anywhere except #rules.

### Example: Watch a Channel Silently

```json
{"channel_name": "announcements"}
```
Auto-reply off. AI reads announcements and saves them to memory.

## Example Commands

- "What's happening in the general channel?"
- "Send a message to #dev saying the deploy is done"
- "Read the last 30 messages in #support"
- "What servers are you in?"

## Troubleshooting

- **Bot offline** — Check token is valid, Message Content Intent is enabled
- **Can't see messages** — Bot needs `Read Message History` and `View Channels` permissions in Discord
- **Tools not available** — Add Discord tools to your active toolset
- **Daemon not firing** — Check filter JSON, try empty `{}` first to confirm events arrive

## Reference for AI

Discord integration for server messaging and monitoring.

SETUP:
- Settings → Plugins → Discord
- Add bot token, test connection
- Multi-account via scopes

AVAILABLE TOOLS:
- discord_get_servers() - list servers and channels
- discord_read_messages(channel?, count?) - read recent messages (1-50, default 20)
- discord_send_message(channel?, text) - send message (max 2000 chars)

DAEMON:
- Source: discord_message
- Filters: mentioned, guild_name, channel_name, username, content_contains, *_not variants
- Task field: auto_reply (boolean) - post AI response back to channel

SCOPES:
- scope_discord ContextVar for multi-account routing
- Sidebar dropdown to switch accounts

TROUBLESHOOTING:
- Bot offline: check token, check Message Content Intent enabled
- No messages: check Discord permissions (Read Message History, View Channels)
- Tools missing: add discord tools to active toolset
