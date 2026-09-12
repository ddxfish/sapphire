# Social

Autonomous emoji reactions and humanized delivery (typos, quote-replies, post-send edits).

## Settings

### Reactions enabled

- **Setting key:** `reaction.enabled`
- **Type:** `boolean`
- **Default:** ON

Let her react to messages with emoji on her own (sentiment-based). OFF: no autonomous reactions — reactions she writes into a reply herself still go through.

### Autonomous silent reactions

- **Setting key:** `reaction.silent_enabled`
- **Type:** `boolean`
- **Default:** ON

React with an emoji without replying.

### Reaction chance (%)

- **Setting key:** `reaction.reaction_chance`
- **Type:** `number`
- **Default:** `10.0`

Chance (0-100%) she silently reacts to an incoming message with an emoji. Messages she isn't going to answer use a fixed 5% roll instead. 0 = never.

### Reaction cooldown (seconds)

- **Setting key:** `reaction.reaction_cooldown_seconds`
- **Type:** `number`
- **Default:** `30`

Minimum seconds between her self-initiated reactions in the same channel. 0 = no cooldown.

### React on reply path

- **Setting key:** `reaction.react_on_reply_path`
- **Type:** `boolean`
- **Default:** ON

ON (default): she may also react to messages she's answering. OFF: react only to messages she's leaving alone.

### Read-only reactions

- **Setting key:** `reaction.read_only_enabled`
- **Type:** `boolean`
- **Default:** ON

React even on messages that will not get a reply.

### Message edits

- **Setting key:** `delivery.message_edits_enabled`
- **Type:** `boolean`
- **Default:** ON

Master switch for editing her own messages after sending — typo fixes, [edit:] corrections, and random afterthoughts. OFF: messages are never edited once sent.

### Auto typos

- **Setting key:** `delivery.auto_typo_enabled`
- **Type:** `boolean`
- **Default:** OFF

Occasionally send a typo, then fix it with an edit.

### Auto typo chance (%)

- **Setting key:** `delivery.auto_typo_chance`
- **Type:** `number`
- **Default:** `12.0`

Chance (0-100%) a reply goes out with a common misspelling, then gets edited to the fix seconds later. Skipped when answering a question; needs Message edits ON.

### Typo fix delay min (seconds)

- **Setting key:** `delivery.auto_typo_delay_min`
- **Type:** `number`
- **Default:** `2.0`

Shortest wait (seconds) before the typo-fix edit lands. Values under 0.5 are treated as 0.5.

### Typo fix delay max (seconds)

- **Setting key:** `delivery.auto_typo_delay_max`
- **Type:** `number`
- **Default:** `6.0`

Longest wait (seconds) before the typo-fix edit. If set below the min, the two swap.

### Smart quote-replies

- **Setting key:** `delivery.quote_reply_enabled`
- **Type:** `boolean`
- **Default:** ON

ON (default): quote-reply when context calls for it — always for questions, usually in busy channels, rarely in DMs, never on jokes or images. OFF: plain messages, no quote header.

### Random post-send edits

- **Setting key:** `delivery.post_send_edit_enabled`
- **Type:** `boolean`
- **Default:** ON

About 4% of replies get a human touch after sending: a subtle typo fixed by edit, or an afterthought ('lol', 'actually') edited in 2-5 seconds later. Needs Message edits ON.

## Reaction sentiment

Custom control for `reaction.sentiment_backend`:

- **VADER (lightweight)** — default; fast sentiment for choosing reaction emoji.
- **Twitter RoBERTa (more accurate)** — better on slang/emoji-heavy chat; needs `transformers` + `torch`. Falls back to heuristics if missing.
