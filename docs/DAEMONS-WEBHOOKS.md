# Daemons & Webhooks

Sapphire can react to events from the outside world — Discord messages, incoming emails, Telegram chats, live phone calls, or HTTP requests from any service. This guide covers how to set them up.

## Quick Start

1. Open the **Triggers** group in the nav — it has five views: Heartbeat, Scheduled, Daemons, Realtime, Webhooks
2. Go to **Daemons** (for Discord/Email/Telegram), **Realtime** (for live phone lines), or **Webhooks** (for HTTP)
3. Click **+ Daemon** / **+ Realtime** / **+ Webhook**
4. Configure the trigger and AI settings
5. Save and enable

(The Heartbeat and Scheduled views are time-driven — see [Continuity](CONTINUITY.md).)

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
| Bot Account | Which Discord bot this daemon listens on (required — only that bot connects) |
| Auto-reply in channel | Send the AI's response back to the Discord channel. Off = listen-only (pipe to TTS, save to memory, act via tools without replying) |
| Reply cooldown (seconds) | Minimum seconds between replies in the same channel. 0 = no limit; 60 = at most once a minute |

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
| Email Account | Which email account this daemon monitors (required — only that account is polled) |
| Auto-reply to sender | Send the AI's response as an email reply. Off = the AI still runs on incoming mail, but no reply goes out |

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
| Account | Which Telegram account to listen on |
| Reply Format | Plain Text, Markdown, HTML, Text + Voice Note, or Voice Note Only |

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

## Realtime

Realtime rules are the live lane. Where a daemon task fires once per event ("a message arrived → run the AI once"), a Realtime rule is an **on/off switch** for a held-open inbound session — a phone call Sapphire answers and converses with in real time. Enabling the rule lets her pick up; no one-shot task ever fires for a realtime source. The rule *gates* the session and supplies its configuration.

Realtime rules live in **Triggers → Realtime**. Under the hood they're stored as daemon tasks whose event source declares itself realtime, so the same account routing and filter matching applies — but the UI keeps the two lanes apart, and realtime rules only appear in the Realtime view.

The **Twilio Voice** plugin ships the first realtime source: **Answer Phone Calls** on a Twilio number. Per-rule config lives on the rule itself:

| Setting | Description |
|---------|-------------|
| Endpoint | Which line the rule answers (e.g. a Twilio number) |
| Callers | Anyone, or an allowlist of numbers. Phone numbers match on their digit tail, so formatting doesn't matter |
| Where the session runs | A saved chat (persistent — she remembers across calls), or an ephemeral per-caller chat that auto-clears after a set number of minutes |
| Greeting | Spoken when the session connects |
| Phone context | A per-turn invisible note so she knows she's on a live call — never saved, never seen by the caller |
| Public line | Apply the conduct rails from the plugin's settings. Uncheck for a trusted line like your own number |
| Toolset elevation | Optional spoken passphrase that unlocks a chosen toolset for the rest of the call |

Rule selection is most-specific-wins: a rule whose caller filter matches the incoming call beats a catch-all rule with no filter — so you can pair a "just me" rule with an "everyone else" rule on the same number. A rule whose filter fails is excluded, and if no rule matches at all, the call is declined.

Full phone setup — Twilio numbers, SIP, voices, ephemeral chats — lives in [PHONE-CALLS.md](PHONE-CALLS.md).

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

**Webhook security:** Every webhook task gets an auto-generated secret. Callers must include the secret in the `x-webhook-secret` header, or use GitHub-style HMAC signatures (`x-hub-signature-256`). The secret is shown in the task's trigger config after creation. For additional security, use unique/random paths (e.g. `github-abc123` instead of `github`).

### Payload Handling

| Method | Content-Type | Sapphire receives |
|--------|-------------|-------------------|
| POST/PUT | `application/json` | Parsed JSON object |
| POST/PUT | anything else | Raw body text |
| GET | — | Query parameters as JSON |

Payloads are capped at **1 MB** — anything larger is rejected with HTTP 413.

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
  -H "x-webhook-secret: YOUR_SECRET_HERE" \
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
  -H "x-webhook-secret: YOUR_SECRET_HERE" \
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
curl -H "x-webhook-secret: YOUR_SECRET_HERE" \
  "https://your-sapphire:8073/api/events/webhook/weather-update?city=Austin&units=imperial"
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

## Reference for AI

Event-driven triggers. UI: **Triggers** nav group — the Daemons, Realtime, and Webhooks views (Heartbeat/Scheduled are time-driven, see CONTINUITY.md).

TYPES:
- daemon: plugin event listener (Discord/email/Telegram). trigger_config: source, account, filter (JSON object), plus plugin-declared task fields (auto_reply, cooldown, reply_format, ...)
- webhook: HTTP trigger at /api/events/webhook/{path}. trigger_config: path, method (GET/POST/PUT), secret (auto-generated on create)
- realtime: a daemon task whose event source declares realtime:true (e.g. Twilio incoming_call). It GATES a live inbound session instead of firing a one-shot task — enabling the rule lets the daemon answer; per-rule config (endpoint/account, caller allowlist, saved vs ephemeral per-caller chat, greeting, phone context, public-line rails, toolset-elevation passphrase) lives on the rule. Rule selection is most-specific-wins (matching filter beats catch-all; no match = decline). Shown in the Realtime view, not Daemons. See PHONE-CALLS.md.

FILTERS (trigger_config.filter):
- exact match is case-insensitive; suffix _contains = substring; suffix _not = exclude; all keys AND'd
- a comma-separated filter value is an allowlist (any entry matches); phone-shaped values compare on digit tail

WEBHOOK:
- auth: x-webhook-secret header, or GitHub-style HMAC x-hub-signature-256 (sha256=hex over raw body); secret shown in trigger config after creation
- payload cap 1 MB (413 above); JSON bodies parsed, other bodies raw text, GET query params as JSON
- success returns {"status": "triggered", ...}; unknown path/method or disabled task = 404

LIMITS: 25 tasks total; 10 daemon; 10 webhook; 4 heartbeat.

TROUBLESHOOTING:
- try an empty filter {} first to confirm events arrive; filter field names must match the daemon's emitted fields
- webhook path is case-sensitive and must match method
- a realtime rule missing from Daemons is correct — realtime-source rules render only in the Realtime view
