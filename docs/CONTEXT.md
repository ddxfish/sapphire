# Context — what fits in her window

Every model has a context window: how much text (counted in tokens) it can read at once. Each turn, Sapphire sends the system prompt, the tool descriptions, the chat history, any documents and pictures, and leaves room for the reply. This page is how she fits all of that, what quietly drops out as a chat grows, and how to see it.

---

## The budget

Before every request (chat turns and task turns alike) one budgeter fits the request to the window.

- **The window** is the smaller of the **context window budget** (`CONTEXT_LIMIT`, default 65535, **Settings → LLM → General**; 0 = no limit of its own) and the size a local server says it's holding (LM Studio, llama.cpp, Ollama).
- **The reply always keeps its room**: its Max Tokens, at least 1,024.
- **When history doesn't fit**, the oldest whole turns go out of view, and the cut takes the request down to about 65% so it isn't redone every turn. She's told: *"[Earlier in this chat: the first N turns are out of your view...]"*. The chat itself is untouched; only the copy sent to the model is shortened.
- **When even an empty history doesn't fit** (a huge prompt, too many tools), nothing is sent and you get plain words saying so.

Tool descriptions, history, tool arguments, Claude's thinking and pictures are all counted. Pictures are counted the way each provider family charges for them. The count is corrected from each provider's own usage reports, and after a "too long" refusal it fits once more with a heavier estimate.

A task or heartbeat has its own **Context window** setting; 0 means use `CONTEXT_LIMIT`.

## Tool results decay

A big tool result (a web page, a file) is useful for a few turns, then it's dead weight. With decay, an old result is replaced by a stub: its first line, how big it was, and how to run it again. Pictures that came with it go too.

- **Settings → LLM → General**, "Old tool results leave her view after N turns" (`TOOL_EXPIRES_AFTER`): turns until any tool result decays. 0 = keep everything.
- A tool or plugin can set its own: web reads and chat documents decay after 3 turns; the notepad and memory tools never do; a browser view decays once a newer one replaces it.
- Decay is decided by how many turns came after a result, never by how full the window is. The live turn is only shortened when there's truly no room.

**The prompt cache** (cloud models and local servers that cache) is protected: while a chat's cache is warm (5 minutes for Claude, an hour with the 1-hour cache, an hour on a local server no other chat is using), new stubs wait, so the cached prefix doesn't change under it.

## Pictures decay

**Settings → Images**, "Turns an image stays visible" (`IMAGE_MEMORY_TURNS`, default 3): how many of your turns she keeps seeing a picture for. 0 = only the turn it arrived in. Each replayed picture costs roughly 1,500 tokens a turn. The pill row above the message box counts down the turns each picture has left.

Uploads are resized to `IMAGE_UPLOAD_MAX_WIDTH` (1920 px).

## Documents

A chat's attached documents get their own share, set in the docs row above the message box: Off, Light (~2K tokens), Normal (~8K) or Heavy (~24K), never more than a quarter of the window. See [CHATS.md](CHATS.md#documents).

---

## Seeing it

**The context bar** above the message box: its full width is your Context limit; the fill is what the last turn actually sent. A notch marks the local server's own window when it's smaller. Hover it for how many turns are out of view and how many results were shortened.

**`/context`** opens the full breakdown, biggest first: system prompt, tool descriptions, your messages, her replies, tool calls, tool results, injected text, images, thinking and framing, plus the biggest tools by description, calls and results. See [SLASH-COMMANDS.md](SLASH-COMMANDS.md).

## When a reply is cut off

**Max Tokens** is the reply's own limit. The default is 8,192 (many models set 16,000), per model in **Settings → LLM**. A reply that hits it keeps what it wrote and gets an amber note: press Continue, ask her to go on, or raise Max Tokens (the **Fix it** button goes there). If it spent the whole budget thinking, the note says that instead. See [NOTIFICATIONS.md](NOTIFICATIONS.md#when-a-reply-fails-fix-it).

## Making room

- `/compact` or `/compact-daily` summarizes old history ([SLASH-COMMANDS.md](SLASH-COMMANDS.md)).
- A leaner toolset sends fewer tool descriptions; `/context` shows which tools cost the most.
- Keep pictures fewer turns, or set documents to Light.
- Raise the context budget if your model and machine can hold more.

See [COSTS.md](COSTS.md) for what this means for a cloud bill.

---

## Reference for AI

BUDGET (core/chat/budget.py): fit() runs before every provider call (chat + task lanes). Window = min(CONTEXT_LIMIT default 65535, local server's loaded size). Reply keeps max_tokens (≥1024). Overflow: oldest whole turns go out of view, cut to ~65%, held behind a watermark; you get "[Earlier in this chat: the first N turns are out of your view...]"; stored history unchanged. No room even with no history = ContextFull, nothing sent. Counts system, tool schemas, history, tool args, thinking, pictures (per provider family); ratio learned from usage; one heavier retry after a too-long refusal. Task "Context window" 0 = CONTEXT_LIMIT.

TOOL DECAY: per-tool expires > file TOOL_EXPIRES > manifest tool_expires > TOOL_EXPIRES_AFTER (0 = keep). Values 'never', N user turns, or superseded-by-group. Stub = first line (200 chars) + size + how to rerun; its pictures go too. By turns after, never by fullness. Shipped: web + documents 3, notepad/memory never, browser views superseded. Warm cache: new stubs wait while the cache is warm. If you need a decayed result, run the tool again.

IMAGES: IMAGE_MEMORY_TURNS 3 (0 = arrival turn only), ~1.5K tokens/picture/turn; IMAGE_UPLOAD_MAX_WIDTH 1920. DOCUMENTS: off 0 / light 2K / normal 8K / heavy 24K per turn, ≤25% of window.

SEE IT: context bar (fill = last turn sent; notch = server window); /context = buckets system, schemas, you, her, calls, results, injected, images, thinking, framing + per-tool. CUTOFF: max_tokens default 8192; hitting it = amber note (Continue / raise Max Tokens in Settings › LLM).
