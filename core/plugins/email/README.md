# Email

Sapphire can read any folder, search, send (to, cc, bcc, pictures), reply, reply-all, forward, save drafts, archive, delete, and handle spam, plus auto-reply to incoming messages. Supports multiple accounts, OAuth2 for Office 365/Exchange, and a privacy-first design where the AI never sees raw email addresses.

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
| `get_inbox` | Latest emails in any folder: inbox, sent, drafts, archive, spam, trash, or one of your own folders |
| `read_email` | Read an email in full by its id: text, who it went to, attachments. She sees attached pictures and can open text attachments |
| `search_emails` | Search a folder by sender, content (subject/body), or date |
| `move_emails` | Move emails to a folder: `archive`, `trash` (recoverable), `spam` (mark as spam), `inbox` (restore, or "not spam") |
| `mark_emails` | Mark emails read, unread, starred or unstarred |
| `send_email` | Send a new email (to, cc, bcc, picture attachments), reply or reply-all (threaded and quoted), or save a draft |
| `forward_email` | Forward an email to contacts, attachments included |
| `get_recipients` | List contacts the AI is allowed to email |

### Email ids

Every email has a stable id like `inbox:4821~k7p`: the folder, the mail server's own number, and a short check code. Listings show it in brackets. An id keeps pointing at the same email no matter what else arrives, and across restarts, so a reply or a delete can never land on the wrong one. The check code ties the id to its mailbox: used on another account, or after the mail server rebuilt the folder, it is refused instead of acting on a different email. Moving an email gives it a new id in the new folder.

### Spam

Sapphire has no spam filter of her own; your mail provider does the filtering. She can open the Spam folder, mark an email as spam (`move_emails` to `spam`) and rescue one (`move_emails` to `inbox`). On Gmail, and on servers set up to learn from the Junk folder, those moves also train the provider's filter.

### Replies

Replies carry the original as a real quoted block, so Gmail and other clients fold it under "..." instead of showing a wall of `>` lines. Reply-all goes to everyone already on the email; turn it off in the plugin settings if you want replies to reach the sender only.

### Privacy Design

The AI **never sees email addresses** in her tools, only display names. This is intentional:
- `get_recipients` shows names from your contacts (people in **Mind → Entities**)
- `send_email` takes contact ids for `to`, `cc` and `bcc`, mapped to real addresses behind the scenes
- A reply uses the email's id and goes to the people already on that thread, no address needed
- Attachments she sends are pictures she already holds: one from the chat (`img:`) on any email, a library photo (`doc:`) only on a new email to contacts you allowed, never on a reply. File paths and web addresses are not attachable: a reply goes to whoever wrote in, and an incoming email could otherwise ask her to send out any picture on this machine or your home network

To allow the AI to email someone, add them as a person in **Mind → Entities** with an **Email** and tick **Allow email**.

**Escape hatch (off by default):** a plugin setting, `allow_all_recipients`, lets `to`, `cc` and `bcc` take raw addresses so the AI can email anyone. It's gated behind a danger-confirm in the email plugin settings. Leave it **off** to keep the guarantee above.

## The Email page

**Apps > Email** is a mail reader of your own, on the same accounts Sapphire uses.

- **Folders** down the side: Inbox, Sent, Drafts, Archive, Spam, Trash, then your own folders, with unread counts.
- **The list**: 50 emails a page, newest first. Show unread only, star, select several and archive, trash, mark or move them together.
- **Search**: type in the box and press Enter. It looks in all your mail (or "This folder"), newest first, and each result says which folder it is in. See "Search words" below.
- **Reading**: click an email. Reply, Reply all, Forward, Archive, Spam (Not spam inside the spam folder), Trash, Move to, Mark unread, Star. Attachments download when clicked.
- **Compose**: To, Cc, Bcc, subject and plain text. A reply quotes the original the same way Sapphire's replies do. "Save draft" files it in Drafts and sends nothing.

Things worth knowing:

- This page is **you**, not the AI. It shows real addresses and can write to anyone; the People whitelist only limits Sapphire's tools.
- **Formatted (HTML) mail is treated as hostile.** Plain text is shown when the email has it. The formatted view is drawn in a sealed frame that runs no scripts and loads nothing from the internet, so a sender cannot tell you opened the mail. "Show remote pictures" loads them for that one email.
- Attachments always download; they never open inside Sapphire.
- Trash and Spam are folders. Nothing on this page deletes mail for good.
- Opening an email marks it read, as any mail program does.
- **The page opens at once.** Each folder's list is kept as it was last seen, in a file on this machine (`user/plugin_state/email_index.db`): who a mail is from and to, its subject, date and flags. The page shows that immediately, also after a reload or a restart, then asks the mail server and updates. **The emails themselves are never stored**, and an opened email is only remembered by the browser tab until you close or reload it.
- You can turn the file off in Settings > Plugins > Email ("Remember mail lists on this machine"). Off deletes what was kept, and the page waits for the server each time. An account you remove is dropped from the file too.
- The page stays logged in to your mail server while you use it (and for five minutes after), so a click is one short exchange, not a new login. It looks for new mail every minute while it is on screen.
- A refused password is not sent again for ten minutes, so a mistyped one cannot get your address locked out by the server. Fixing the password in Settings tries again at once.
- Not on this page yet: attaching a file to a new email, and reopening a saved draft to keep writing it.

## Search words

The same words work in the Email page's search box and in Sapphire's `search_emails(query=...)`. Every term must match.

| Type | Finds |
|------|-------|
| `invoice` | mail with that word anywhere |
| `"quarterly numbers"` | that exact phrase |
| `from:ann` `to:krem` `cc:dan` | who it is from or to: a name or part of an address |
| `subject:invoice` `body:refund` | where the word is |
| `is:unread` `is:read` `is:starred` `is:answered` | its state |
| `has:attachment` | mail with a file attached |
| `after:2026-11-01` `before:2026-12-01` `on:2026-11-18` | by date (`today` and `yesterday` work too) |
| `newer_than:7d` `older_than:2m` | by age: d days, w weeks, m months, y years |
| `in:sent` `in:spam` `in:all` | one folder, or everywhere |
| `-from:newsletter` `-word` | leave these out |

- Searching everywhere skips Spam and Trash; name them (`in:spam`) to look there.
- **Gmail accounts are searched with Gmail's own engine**, so anything Gmail's search box understands also works there (`label:`, `filename:`, `larger:`, `OR`). Other mail servers get the standard search, which has no `OR`.
- The search always asks the mail server; nothing is searched on this machine.

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

Auto-replies are threaded and quoted like any other reply. New mail that arrived while Sapphire was off fires when she starts again.

Who gets an automatic reply, whatever the task's prompt says:

- **Only people in People**, unless you turn on "Auto-reply to anyone" in Settings > Plugins > Email. Nobody is in the loop when a task answers, and anyone can write in.
- **She can decline.** If her answer contains `<<PASS>>`, nothing is sent for that email. Tell her so in the task's prompt.
- Never newsletters, mailing lists, bounces or other autoresponders (the event still fires, with `bulk` set, so she can file them).
- Never the same email twice, one address per reply, and at most 10 automatic replies a day to one sender: the backstop against a responder that never says it is automatic.
- Mail she sends with her tools during such a task is marked as automatic too, and she is refused when replying to automated mail there.

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
| `bulk` | `true` for newsletters, lists and automated mail |

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
- "Read the one from Sarah"
- "Anything in my spam folder?"
- "That one is spam" / "That is not spam, put it back"
- "Find emails from fish last week"
- "Search my inbox for anything about the invoice"
- "Reply to that email saying I'll be there at 5"
- "Reply to everyone on that thread"
- "Send an email to Sarah about the meeting tomorrow, cc Bob, and attach that picture"
- "Draft a reply but don't send it"
- "Forward the invoice to Sarah"
- "Archive the newsletters and delete the two promos"
- "Star that one and mark it unread"
- "Who can I email?"

## Troubleshooting

- **Connection failed** — Check IMAP/SMTP server addresses and ports
- **Gmail blocked** — Use an App Password, not your regular password
- **Can't send to someone** — In Mind → Entities, give them an Email and tick Allow email
- **Daemon not firing** — Check poll interval, verify account is connected
- **Tools not available** — Add Email tools to your active toolset

## Reference for AI

Email integration with IMAP/SMTP, multi-account, privacy-first design.

SETUP:
- Settings → Plugins → Email
- Add IMAP/SMTP server, email, password (or OAuth2)
- Gmail: requires App Password

AVAILABLE TOOLS:
- get_inbox(folder?, count?, unread_only?) - list a folder (inbox/sent/drafts/archive/spam/trash or a folder name); each line starts with the email id, e.g. [inbox:4821~k7p]. Copy an id whole: the part after ~ belongs to it
- read_email(id, page?, attachment?) - full email; long bodies are paged; attachment=N opens a picture or text file
- search_emails(sender?, content?, date?, folder?, count?) - sender matches name+address substring, content=subject OR body, date=YYYY-MM-DD returns ~10 each side
- move_emails(ids, to) - to=archive / trash (recoverable) / spam (mark as spam) / inbox (restore, not-spam) / a folder name
- mark_emails(ids, mark) - read / unread / starred / unstarred
- send_email(body, to? | reply_to?, reply_all?, subject?, cc?, bcc?, attach?, draft?) - exactly one of to (contact ids) or reply_to (an email id); attach = pictures you hold (img:<id>; doc:<N> only on a new email, not a reply); draft=true saves instead of sending
- forward_email(id, to, cc?, note?) - attachments carried
- get_recipients() - whitelisted contacts (ids + names, no addresses)

EMAIL PAGE (Apps > Email, app/index.js + routes/mail.py):
- The person's own mail reader: folders with counts, 50 per page, search, read, reply/reply-all/forward, compose, drafts, move, mark. Not a tool surface; the AI does not use it.
- The page shows real addresses and is not limited by the People whitelist (that gate is on the tools only).
- Routes under /api/plugin/email/: GET folder, GET message, GET attachment (always a download), POST move, POST mark, POST send. One request = one IMAP session; nothing cached.
- HTML mail renders only in a sandboxed iframe with a no-remote-load policy; remote pictures are per-email opt-in.
- Same ids as the tools (folder:uid). The browser sends back the folder's UIDVALIDITY with each id; the page never reads or writes the tools' stored validity mark.

IDS:
- Emails are addressed by id (folder:number), never by position; ids stay valid across calls and chats
- A daemon event's UID line is the same id: read_email("inbox:4821~k7p")
- In a new-email task with auto-reply on, your final answer is emailed to the sender; put <<PASS>> in it to send nothing
- read_email marks a sender "(not in your contacts)" and warns when replies would go to someone other than the sender

PRIVACY:
- AI never sees raw email addresses in tool results
- to/cc/bcc gated by contacts (person entities with email + allow_email); raw addresses need allow_all_recipients
- A reply or reply-all goes to the people already on the thread (reply-all can be turned off in settings)

DAEMON:
- Source: email_message
- Polls IMAP on interval (default 120s, min 30s, configurable)
- Filters: from_address, from_name, to_address, subject_contains, snippet_contains, account, bulk
- Task field: auto_reply (boolean) - send AI response as a threaded, quoted reply; never sent to bulk/automated mail
- New mail is tracked by a saved high-water mark, so mail that arrived while down still fires

SCOPES:
- scope_email ContextVar for multi-account
- One IMAP/SMTP config per scope

TROUBLESHOOTING:
- Gmail: use App Password (Security → 2FA → App Passwords)
- O365: use OAuth2 auth method
- Can't send: recipient needs email + allow_email on their person card
