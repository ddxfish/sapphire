# Daemons & Webhooks

Sapphire can react to events from the outside world — Discord messages, incoming emails, Telegram chats, or HTTP requests from any service. This guide covers how to set them up.

## Quick Start

1. Go to **Schedule** in the nav
2. Click **+ New Task**
3. Choose **Daemon** (for Discord/Email/Telegram) or **Webhook** (for HTTP)
4. Configure the trigger and AI settings
5. Save and enable

---

## Daemons

Daemons are background listeners that react to events from connected platforms. When a message arrives (Discord, email, Telegram), Sapphire can process it with AI and optionally reply.

### How It Works

1. A plugin runs a background listener (e.g. Discord bot watching for messages)
2. When an event arrives, Sapphire checks your daemon tasks
3. If the event matches your filters, the AI runs with your configured prompt/tools
4. If auto-reply is on, the response goes back to the source

### Setting Up a Daemon Task

**Trigger section:**
- **Source** — which event to listen for (e.g. "Discord Message", "New Email")
- **Filter** — JSON object to narrow which events fire the task
- **Source Settings** — plugin-specific options (e.g. auto-reply toggle)

**AI section:**
- Prompt, toolset, voice, scopes — same as any scheduled task

### Filters

Filters are a JSON object. Every key must match for the event to fire. Leave empty to match all events.

**Match types:**

| Suffix | Behavior | Example |
|--------|----------|---------|
| *(none)* | Exact match (case-insensitive) | `{"channel_name": "general"}` |
| `_contains` | Substring match | `{"content_contains": "help"}` |
| `_not` | Exclude match | `{"channel_name_not": "spam"}` |

**Combine them freely — all conditions are AND'd:**

```json
{"mentioned": "true", "channel_name": "general"}
```
Only fires when the bot is @mentioned AND the message is in #general.

```json
{"mentioned": "true", "channel_name_not": "help", "username_not": "other-bot"}
```
Fires on mentions in any channel except #help, ignoring messages from "other-bot".

```json
{"subject_contains": "invoice", "from_name": "accounting"}
```
Fires on emails with "invoice" in the subject from someone named "accounting".

---

## Discord

### Available Filters

| Filter | What it matches |
|--------|----------------|
| `mentioned` | `"true"` or `"false"` — was the bot @mentioned |
| `guild_name` | Server name |
| `channel_name` | Channel name |
| `username` | Message author's username |
| `content_contains` | Substring in message text |
| `channel_name_not` | Exclude a channel |
| `guild_name_not` | Exclude a server |
| `username_not` | Exclude a username |
| `guild_id` | Server ID (advanced) |
| `channel_id` | Channel ID (advanced) |

### Source Settings

| Setting | Description |
|---------|-------------|
| Auto-reply in channel | Send the AI's response back to the Discord channel |

### Example: Server Helper Bot

Respond when mentioned in any channel except #rules:

```
Filter: {"mentioned": "true", "channel_name_not": "rules"}
Message: "You are a helpful server assistant. Answer the user's question briefly."
Auto-reply: On
Toolset: default
```

### Example: Log All Messages in a Channel

Watch #announcements and save to memory, no reply:

```
Filter: {"channel_name": "announcements"}
Message: "Summarize this announcement and save it to memory."
Auto-reply: Off
Toolset: default (needs memory tools)
Memory scope: server-logs
```

### Example: Keyword Alert

React when someone says "bug" in #dev:

```
Filter: {"channel_name": "dev", "content_contains": "bug"}
Message: "A bug was reported. Acknowledge it and suggest next steps."
Auto-reply: On
```

---

## Email

### Available Filters

| Filter | What it matches |
|--------|----------------|
| `from_address` | Sender email address |
| `from_name` | Sender display name |
| `to_address` | Recipient email address |
| `subject_contains` | Substring in subject line |
| `snippet_contains` | Substring in email body |
| `account` | Which email account scope |

### Source Settings

| Setting | Description |
|---------|-------------|
| Auto-reply to sender | Send the AI's response as an email reply |

### Example: Auto-Reply to Support Emails

```
Filter: {"to_address": "support@mysite.com"}
Message: "You are a support agent. Read this email and write a helpful reply. Be professional and concise."
Auto-reply: On
Prompt: support-agent
Email scope: support
```

### Example: Invoice Processor

```
Filter: {"subject_contains": "invoice", "from_address_not": "noreply@spam.com"}
Message: "An invoice arrived. Extract the amount, vendor, and due date. Save to knowledge."
Auto-reply: Off
Toolset: default
Knowledge scope: finances
```

---

## Telegram

### Available Filters

| Filter | What it matches |
|--------|----------------|
| `chat_id` | Telegram chat ID |
| `username` | Sender's username |
| `chat_type` | "private", "group", or "supergroup" |

### Source Settings

| Setting | Description |
|---------|-------------|
| Account | Which Telegram bot to listen on |
| Reply Format | Plain text, Markdown, or HTML |

### Example: Personal Assistant on Telegram

```
Filter: {"chat_type": "private"}
Message: "You are my personal assistant on Telegram. Help with whatever I ask."
Account: personal-bot
Reply Format: Markdown
Toolset: default
Memory scope: personal
```

---

## Webhooks

Webhooks let external services trigger Sapphire via HTTP. Any service that can send an HTTP request (GitHub, monitoring tools, CI/CD, cron jobs, IFTTT, Home Assistant) can talk to Sapphire.

### How It Works

1. You create a webhook task with a path and method
2. Sapphire listens at `https://your-sapphire/api/events/webhook/{path}`
3. When a request arrives, the payload is sent to the AI along with your prompt
4. Sapphire processes it and returns `{"status": "triggered"}` to the caller

### Setting Up a Webhook Task

**Trigger section:**
- **Path** — the URL path (e.g. `deploy` → `/api/events/webhook/deploy`)
- **Method** — GET, POST, or PUT

**The webhook URL is unauthenticated** — anyone with the URL can trigger it. Use unique/random paths for security (e.g. `github-abc123` instead of `github`).

### Payload Handling

| Method | Content-Type | Sapphire receives |
|--------|-------------|-------------------|
| POST/PUT | `application/json` | Parsed JSON object |
| POST/PUT | anything else | Raw body text |
| GET | — | Query parameters as JSON |

### Example: GitHub Deploy Notification

**Webhook task:**
```
Path: github-deploy
Method: POST
Message: "A deployment just happened. Summarize it and let me know if anything looks wrong."
Chat: deployments
Voice: Off
```

**GitHub webhook config:**
```
URL: https://your-sapphire:8073/api/events/webhook/github-deploy
Content-Type: application/json
Events: Deployments
```

**Test it:**
```bash
curl -X POST https://localhost:8073/api/events/webhook/github-deploy \
  -H "Content-Type: application/json" \
  -d '{"service": "api", "status": "success", "version": "v2.1.0"}'
```

### Example: Server Monitoring Alert

```
Path: monitor-alert-x7k9
Method: POST
Message: "A server alert was received. Assess the severity and recommend action. If critical, save to knowledge as an incident."
Toolset: default
Knowledge scope: incidents
```

**Trigger from monitoring tool:**
```bash
curl -X POST https://your-sapphire:8073/api/events/webhook/monitor-alert-x7k9 \
  -H "Content-Type: application/json" \
  -d '{"host": "db-primary", "alert": "disk usage 92%", "level": "warning"}'
```

### Example: Daily Weather via GET

```
Path: weather-update
Method: GET
Message: "Look up the weather for today and tell me what to wear."
Toolset: default (needs web search)
Voice: On
```

**Trigger from cron or IFTTT:**
```bash
curl "https://your-sapphire:8073/api/events/webhook/weather-update?city=Austin&units=imperial"
```

### Example: Home Assistant Event

```
Path: ha-motion-detected
Method: POST
Message: "Motion was detected. Check the camera and tell me what you see."
Toolset: homeassistant
```

---

## AI Configuration (All Types)

Every daemon and webhook task has the same AI settings as scheduled tasks:

| Setting | Description |
|---------|-------------|
| **Message** | The instruction sent to the AI along with the event data |
| **Persona** | Pre-built profile (auto-fills prompt, toolset, voice, scopes) |
| **Prompt** | Which character prompt to use |
| **Toolset** | Which tools the AI can use |
| **Provider / Model** | Which LLM (auto = current default) |
| **Chat** | Save conversation to a named chat (blank = ephemeral) |
| **Voice** | Speak on server speakers and/or browser |
| **Mind scopes** | Memory, knowledge, people, goals — per-task isolation |
| **Execution limits** | Context window, parallel tools, tool rounds |

### Tips

- **Use a named chat** if you want to see the conversation history in the UI
- **Set a persona** to quickly configure prompt + tools + voice together
- **Use scopes** to isolate daemon memory from your personal chats
- **Toolset matters** — a daemon without tools can only talk, not act

---

## Limits

| | Max |
|---|---|
| Total tasks (all types) | 25 |
| Daemon tasks | 10 |
| Webhook tasks | 10 |
| Heartbeat tasks | 4 |

---

## Troubleshooting

**Daemon task not firing?**
- Check the filter JSON is valid (the editor validates on save)
- Check filter values are case-insensitive but must match the event field names exactly
- Try with an empty filter `{}` first to confirm events are arriving
- Check the plugin is enabled and connected (Discord bot online, email polling active)

**Webhook returning 404?**
- Path and method must match exactly (path is case-sensitive)
- Task must be enabled
- Check the full URL: `/api/events/webhook/{your-path}`

**AI not replying to Discord/Telegram?**
- Make sure "Auto-reply" is enabled in Source Settings
- Check the toolset includes the platform's tools (e.g. discord toolset for Discord)
- Check the logs for errors in the AI response

**Filter not matching?**
- All filter keys are AND'd — every one must pass
- Use `_contains` for partial matches instead of exact
- Field names must match what the daemon emits (check the filter hints in the editor)
