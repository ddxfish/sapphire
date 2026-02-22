# People

Sapphire can remember the people in your life — names, relationships, phone numbers, emails, addresses, and notes. The AI uses this to personalize conversations and, if you allow it, send emails to your contacts.

People are managed in the **Mind** view (brain icon) under the **People** tab.

---

## Adding People

### From the Web UI

1. Open **Mind** → **People** tab
2. Click **+ Add Person**
3. Fill in the fields you want:

| Field | Purpose |
|-------|---------|
| Name | Required. Unique per scope (case-insensitive) |
| Relationship | How you know them (friend, coworker, dad, etc.) |
| Phone | Phone number |
| Email | Email address |
| Address | Physical address |
| Notes | Anything else — birthday, preferences, context |
| Allow AI to send email | Checkbox — whitelists this person for the email tool |

### From the AI

The AI can save people during conversation using the `save_person` tool. It creates or updates by name — if a person with that name already exists in the current scope, it updates their info.

### VCF Import

Bulk import contacts from a vCard (.vcf) file:

1. Mind → People tab → **Import VCF**
2. Select your .vcf file
3. Sapphire parses name, phone, email, address, notes, org, and title
4. Duplicates (same name + email in scope) are skipped
5. Extra phone numbers and emails go into the notes field

---

## Privacy & Email Whitelisting

**How email sending works:**
1. You add a person and check **"Allow AI to send email"**
2. The AI calls `get_recipients()` — gets back a list of `{id, name}` pairs (no addresses)
3. The AI calls `send_email(recipient_id=3, ...)` — Sapphire looks up the actual address server-side
4. The email is sent without the AI ever knowing the address

This prevents prompt injection or confused AI from sending emails to arbitrary addresses.

---

## Scopes

People are **scoped** — each chat can access a different set of contacts via the **People scope** in Chat Settings sidebar.

- **Default**: All chats share the "default" people scope
- **Custom scopes**: Create scopes for isolation (e.g., "work" vs "personal")
- **Global overlay**: A scope sees its own people plus any in the "global" scope
- **None**: Set people scope to "none" to disable people access for a chat

Create new scopes with the **+** button next to the People scope dropdown in the sidebar.

---

## Search

The AI searches people using the same cascading strategy as knowledge:

1. Full-text search (name, relationship, notes)
2. Vector similarity (threshold: 0.55 — stricter than knowledge)
3. Substring fallback

When the AI calls `search_knowledge`, it searches both people AND knowledge entries, returning combined results.

---

## Reference for AI

People system for contact management with privacy-first email integration.

TOOLS:
- save_person(name, relationship?, phone?, email?, address?, notes?) — upsert by name per scope
- search_knowledge(query) — searches people + knowledge combined
- delete_knowledge(entry_id?) — can delete people entries
- get_recipients() — returns [{id, name}] of email-whitelisted contacts (no addresses)
- send_email(recipient_id, subject, body) — sends to whitelisted contact by ID

FIELDS:
- name (required, unique per scope case-insensitive)
- relationship, phone, email, address, notes (all optional text)
- email_whitelisted (boolean — controls email tool access)
- scope (via scope_people ContextVar)

PRIVACY MODEL:
- get_recipients returns IDs + names only
- send_email requires recipient_id (looked up server-side)
- Only email_whitelisted=true people appear in recipient list

SCOPES:
- Scoped via scope_people ContextVar
- Global overlay: scope sees own + 'global' entries (read-only for AI)
- AI cannot write to the 'global' scope — only users can via the UI
- Set per-chat in sidebar Mind Scopes → People

VCF IMPORT:
- POST /api/knowledge/people/import-vcf (multipart form)
- Parses: FN, TEL, EMAIL, ADR, NOTE, ORG, TITLE
- Deduplicates by (name, email) per scope

SEARCH:
- FTS on name, relationship, notes
- Vector similarity threshold: 0.55 (stricter than knowledge at 0.40)
- Combined with knowledge results in search_knowledge tool
