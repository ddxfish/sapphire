# Email

Sapphire can read your inbox, send emails, and auto-reply to incoming messages. Supports multiple accounts, OAuth2 for Office 365/Exchange, and a privacy-first design where the AI never sees raw email addresses.

## Setup

1. Open Settings → Plugins → Email
2. Click "Add Account"
3. Enter your email server details:
   - **IMAP Server** — e.g., `imap.gmail.com`, `outlook.office365.com`
   - **SMTP Server** — e.g., `smtp.gmail.com`, `smtp.office365.com`
   - **Email Address**
   - **Password** or **OAuth2 token** (for O365/Exchange)
4. Test the connection

### Gmail Users

Gmail requires an **App Password** (not your regular password). You must have **2-Step Verification enabled first** — App Passwords won't appear without it.

1. Go to [Google Account → Security](https://myaccount.google.com/security)
2. Enable **2-Step Verification** if not already on
3. Go back to your Google Account and **search "App Passwords" in the search bar** at the top — this is the easiest way to find it (the setting is buried)
4. Create an app password — select "Mail" or name it "Sapphire"
5. Google gives you a 16-character password — copy it
6. Use that password in Sapphire (not your regular Google password)

### Office 365 / Exchange

Sapphire supports OAuth2 (XOAUTH2) for Microsoft accounts. Select OAuth2 as the auth method and provide your access token. Tokens auto-refresh with a 60-second buffer.

## Available Tools

| Tool | What it does |
|------|--------------|
| `get_inbox` | Fetch latest emails from inbox, sent, or archive (up to 50) |
| `read_email` | Read the full text of an email by its index |
| `archive_emails` | Move emails to the Archive folder |
| `get_recipients` | List contacts the AI is allowed to email |
| `send_email` | Send a new email or reply to one |

### Privacy Design

The AI **never sees email addresses** — only display names. This is intentional:
- `get_recipients` shows names from your People contacts (Knowledge → People)
- `send_email` uses a `recipient_id` that maps to the real address behind the scenes
- Replies use `reply_to_index` from the inbox, no address needed

To allow the AI to email someone, add them as a contact in **Knowledge → People** with their email address.

## Multi-Account

Multiple email accounts are supported via scopes.

1. Add accounts in Settings → Plugins → Email
2. Switch using the Email scope dropdown in Chat Settings
3. Each scope routes to a different IMAP/SMTP account

## Daemon (Auto-React to Emails)

Sapphire polls your inbox on an interval and can trigger AI processing when new emails arrive.

### Quick Setup

1. Go to **Schedule** → **+ New Task** → choose **Daemon**
2. Set source to **New Email**
3. Configure filters
4. Enable **Auto-reply to sender** if you want the AI to respond by email

### Poll Interval

Set in Settings → Plugins → Email. Default is 120 seconds, minimum 30 seconds. Lower intervals mean more IMAP connections.

### Filters

| Filter | What it matches |
|--------|----------------|
| `from_address` | Sender's email address |
| `from_name` | Sender's display name |
| `to_address` | Recipient address |
| `subject_contains` | Substring in subject line |
| `snippet_contains` | Substring in email body |
| `account` | Which email scope |

### Example: Support Auto-Responder

```json
{"to_address": "support@mysite.com"}
```
Auto-reply on, prompt set to a support agent persona. Responds to all support emails.

### Example: Invoice Watcher

```json
{"subject_contains": "invoice", "from_address_not": "noreply@spam.com"}
```
Auto-reply off. AI extracts invoice details and saves to knowledge.

## Example Commands

- "Check my email"
- "Read email #3"
- "Reply to that email saying I'll be there at 5"
- "Send an email to Sarah about the meeting tomorrow"
- "Archive emails 1, 2, and 5"
- "Who can I email?"

## Troubleshooting

- **Connection failed** — Check IMAP/SMTP server addresses and ports
- **Gmail blocked** — Use an App Password, not your regular password
- **Can't send to someone** — Add them to People contacts with an email address first
- **Daemon not firing** — Check poll interval, verify account is connected
- **Tools not available** — Add Email tools to your active toolset

## Reference for AI

Email integration with IMAP/SMTP, multi-account, privacy-first design.

SETUP:
- Settings → Plugins → Email
- Add IMAP/SMTP server, email, password (or OAuth2)
- Gmail: requires App Password

AVAILABLE TOOLS:
- get_inbox(count?, folder?) - fetch emails (1-50, default 20, folders: inbox/sent/archive)
- read_email(index) - read full email by 1-based index from get_inbox
- archive_emails(indices) - move emails to archive by index array
- get_recipients() - list whitelisted contacts (names only, no addresses)
- send_email(recipient_id?, reply_to_index?, subject, body) - send or reply

PRIVACY:
- AI never sees raw email addresses
- Recipients gated by People contacts in Knowledge
- send_email uses recipient_id (mapped internally) or reply_to_index

DAEMON:
- Source: email_message
- Polls IMAP on interval (default 120s, min 30s, configurable)
- Filters: from_address, from_name, to_address, subject_contains, snippet_contains, account
- Task field: auto_reply (boolean) - send AI response as email reply

SCOPES:
- scope_email ContextVar for multi-account
- One IMAP/SMTP config per scope

TROUBLESHOOTING:
- Gmail: use App Password (Security → 2FA → App Passwords)
- O365: use OAuth2 auth method
- Can't send: add recipient to People contacts first
