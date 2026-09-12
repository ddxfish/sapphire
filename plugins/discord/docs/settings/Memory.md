# Memory

Plugin-local people memory and ambient learning. Data stays in the Discord plugin SQLite—not Sapphire core Mind.

## Settings

### Memory

- **Setting key:** `profile.enabled`
- **Type:** `boolean`
- **Default:** ON

ON (default): she builds per-user memory from Discord — facts, birthdays, and names, kept per bot account in the plugin's own database (never in her core Mind). OFF: no new capture and replies stop using stored facts; existing data stays until you Forget users below.

### Learn from ambient chat

- **Setting key:** `profile.ambient_distill_enabled`
- **Type:** `boolean`
- **Default:** OFF

OFF (default): only explicit facts (/remember, tools, UI) and lightweight interests. ON: buffer member chat and periodically distill durable personal facts with an LLM into the plugin DB (never core Mind).

### Ambient distill interval (hours)

- **Setting key:** `profile.ambient_distill_interval_hours`
- **Type:** `number`
- **Default:** `1.0`

How often distill may run per bot account. Default 1 hour. Cron checks every 15 minutes; values below 0.25 are clamped to 15 minutes.

### Min buffered messages before distill

- **Setting key:** `profile.ambient_distill_min_messages`
- **Type:** `number`
- **Default:** `8`

A person needs at least this many new buffered lines before the LLM distill runs for them.

### Max new facts per distill pass

- **Setting key:** `profile.ambient_distill_max_facts`
- **Type:** `number`
- **Default:** `3`

Upper bound on facts written for one person in a single distill pass.

Relationship-aware timing and channel situation live on the **[Cognition](Cognition.md)** tab (`profile.relationship_policy_*`, `cognitive.situation_*`).

## People She Knows (browser)

Custom Memory UI (not a setting field):

- Pick a **bot account**, then browse users the plugin has seen.
- **Browse** expands facts (pin / edit / soft-forget / restore), milestones, and interest topics.
- **Forget** permanently deletes that user’s profile, facts, milestones, interests, and their pinned memories for this bot (GDPR-style wipe in the plugin DB).

Everything here is **per bot account** in the plugin SQLite—never Sapphire core Mind.

## Ambient distill review

Queue of unpinned facts learned from ambient chat (`source=ambient_distill`):

- **Pin (approve)** keeps the fact as durable memory
- **Soft-forget** hides junk without wiping the person
- Optional **Include already pinned** to revisit approved facts
- Uses the same bot account selector as People She Knows (`GET profiles/facts/review`)

## Shared Server Lore

Guild/channel facts separate from per-user profiles (example: “deploy day is Thursday”).

- **Refresh servers** loads connected guilds/channels (same source as Greeting Channels).
- Choose a **server** and optional **channel**, enter lore, **Add lore**.
- Leave channel on “Whole server” for guild-wide lore.
- Soft-forget hides a lore row without deleting it; restore brings it back.
- Lore is injected into reply prompts for matching guild/channel context.

## Memory test pathways

Operator helpers that write only into plugin SQLite:

| Button | What it does |
|--------|----------------|
| Seed milestone | Inserts a test relationship milestone |
| Simulate return + interests | Fakes a long gap + a topical message |
| Seed lore | Adds sample server lore |
| Seed interest | Bumps an interest topic |
| Preview context | Shows profile/lore/milestone/interest payload |
| Run ambient distill | Seeds sample chat lines and force-runs distill for the user id |

Also see [pluginflow.md](../pluginflow.md) for the overall memory/world-model flow.
