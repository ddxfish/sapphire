# People — entities and contacts

The people, places, projects and things in your world each get a card in **Mind → Entities**. Sapphire links memories to them as she saves, so asking about someone pulls what she knows about them. Person cards double as the contact book for the email and phone tools. Entities are the entity layer of the [Mind Palace](MEMORY.md).

<img width="50%" alt="sapphire-people" src="https://github.com/user-attachments/assets/77b1df57-a0cf-4666-8bfe-4dcb5ce96579" />

---

## Cards

**+ New entity** picks a kind (Person, Place, Thing, Event, Other) and opens its card. Each kind has its own fields; **+ Add fact** adds a free line. A card also shows its connections, how often memories mention it, and recurring dates: a birthday shows with ↻ and returns to the Upcoming strip every year.

Person cards have:

| Field | Purpose |
|---|---|
| Relationship | How you know them |
| Nicknames | Comma-separated. A memory that says "the boss" links to this card |
| Phone, Email, Address, Birthday | Contact details |
| Notes, Background, Interests, Voice & humor, Likes, Dislikes | What she should know about them |
| **Allow email** | Lets the email tools send to this person (off by default) |
| **Allow phone calls** | Lets the phone tool call this person (off by default) |

**Your own kinds:** drop a JSON kind template into `user/mind_palace/templates/` and it appears in the kind list (a template with a built-in kind's name replaces it).

## How she uses them

- **Saving** a memory that names someone links it to their card automatically, nicknames included (whole words, case-insensitive, the longest name wins, and a real name beats another card's nickname).
- **`entity=`** on a save files a fact on that card directly. A name that only partly matches still saves, and her reply asks "did you mean…".
- **`list_entities`** gives her the roster, or one kind's cards with their fields.
- She can't merge or delete entities. Merging is yours: **Mind → Settings → Maintenance → Human dedup → Entities**, which keeps the loser's name as a nickname.

---

## Contacts: email and phone

The email and phone plugins never see addresses or numbers. They see only the people you allowed:

1. On a person card, tick **Allow email** (or **Allow phone calls**).
2. She calls `get_recipients()` and gets back `{id, name}` pairs, with no addresses.
3. She sends with `send_email(recipient_id=…)` or calls with `phone_call`; Sapphire looks up the real address or number itself.

So a confused model or a prompt injection can't email or call an arbitrary address. If the memory engine can't answer, the contact list comes back empty rather than guessing.

**Which cards count:** the chat sidebar's **contacts** selector (the `people` scope) picks whose person cards the email and phone tools see. It's separate from the memory scope on purpose, so one contact book can serve several minds.

---

## Scopes

Entities ride the chat's memory scope, like the rest of the Mind (see [Scopes](MEMORY.md#scopes)). The contacts selector above is the one exception.

---

## Reference for AI

ENTITIES = Mind Palace entity layer (Mind → Entities): person/place/thing/event/other cards with template fields, nicknames, connections, mention counts, recurring dates (Upcoming strip). User templates: user/mind_palace/templates/*.json.

TOOLS:
- save_memory(content, entity=NAME) — file a fact on a card; nicknames resolve; a partial match saves + asks "did you mean". save_memory(layer='entities', entity=…) makes the fact a card chunk.
- list_entities(kind?, limit?) — roster (name, kind, nicknames, fact count); with kind, the filled-in fields (relationship, birthday…).
- search_memory finds entities too; depth=1 follows a card's connections.
- Merge/delete entities: UI only (Mind → Settings → Maintenance → Human dedup). No save_person: that was the legacy engine.

CONTACTS (core/contacts.py, provider = the active memory engine; fails closed on error):
- Person fields allow_email / allow_call gate the tools.
- get_recipients() [email] → [{id, name}] of allowed contacts, no addresses; send_email(recipient_id, subject, body); phone_call [twilio-voice] resolves the number server-side the same way.
- Which cards: the chat's `people` scope (sidebar "contacts"), separate from memory_scope.
