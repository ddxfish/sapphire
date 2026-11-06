# Notifications and "Fix it"

Everything Sapphire wants to tell you that isn't a chat message lands in one place: a download finished, a plugin failed to load, a backup went out, a daemon fired, a provider switch failed. When something goes wrong in a chat, the error says what happened in plain words and, where it can, offers a **Fix it** button that takes you to the setting that fixes it.

---

## The notification center

Open it from the nav rail: **System → 🔔 Notifications**.

- **Filters**: All, Active, Critical, Error, Warning, Success, Info, plus a filter by source.
- **Opening the page marks everything read.** A row with a destination is a link: click it to go there (a settings tab, the Import page, the Chat Manager...).
- **Clear all** removes finished rows. A row that's still running (a download, an import) stays.
- **Progress rows**: long jobs (model downloads, wake word models, voice installs, Mind imports, provider switches) show a bar that fills as they go and settles as done or failed.

The chat sidebar shows the latest rows and the unread counts under the chat dropdown.

### Levels

| Level | What you see |
|---|---|
| Info | Kept on the page only, no pop-up |
| Success | A short toast |
| Warning | A toast and a yellow count |
| Error | A toast and a red count |
| Critical | A toast that stays until you click it, and a red count |

The gear on the page sets **Toast seconds** (`NOTIFY_TOAST_SECONDS`, 4) and **Toast from level** (`NOTIFY_TOAST_MIN_LEVEL`, success): the lowest level that pops up.

### What's kept

The last 500 rows, in `user/notifications.json`; running rows are never pruned. A repeat of the same event updates its row instead of stacking a new one. Rows never carry the name of a private chat, and anything that comes from a private chat shows as a brief toast only.

---

## When a reply fails: Fix it

A turn that fails doesn't show a stack trace. The failed reply says what went wrong in the app's own words, with a **Fix it** button when there's a place to go and the raw error folded under **Details**. It covers, among others:

- the conversation is too long for the model's context, or Max Tokens is set too high;
- the request timed out, or the connection dropped mid-reply;
- LM Studio, Ollama or the server can't be reached; LM Studio has no model loaded or failed to load one; Ollama doesn't have the model;
- out of quota or credits, rate-limited, a bad API key, or the provider is overloaded;
- an image or request too big, or a model that can't see images;
- a missing model or a bad base URL.

**Fix it** opens Settings → LLM, scrolled to the provider card or the setting in question (Context limit, Request timeout...).

**A reply cut off by its length limit** keeps its text and gets an amber note: press Continue, ask her to go on, or raise **Max Tokens** for that model (Fix it takes you there). If the whole budget went to thinking, the note says so. See [CONTEXT.md](CONTEXT.md).

Errors from the rest of the app read the same way: "Sapphire couldn't *do the thing*. Try again…" with a short reason. Notification rows can carry a Fix it destination too: plugins, STT, TTS, wake word, embedding, devices, backup, dashboard, the Self page, Import and the Chat Manager.

---

## For plugin authors

`core.notify` is the one way to post a row, a progress bar or a toast. See [plugin-author/providers.md](plugin-author/providers.md) and [plugin-author/web-ui.md](plugin-author/web-ui.md).

---

## Reference for AI

NOTIFICATION CENTER (core/notify.py; System → Notifications): rows in user/notifications.json (ring of 500, active rows never pruned; once-keys ledger user/notifications-once.json). Levels info (log only) / success (toast) / warning (yellow count) / error (red) / critical (sticky toast). States open/active/done/failed; progress rows (begin/tick/finish, progress(), tqdm_for). Same key = same row updated. private=True → flash only; rows never name a chat. Settings NOTIFY_TOAST_SECONDS 4, NOTIFY_TOAST_MIN_LEVEL success. Routes /api/notifications (API.md).

FIX IT: a failed turn's row carries metadata.error from core/chat/llm_errors.explain → {text, go, detail}; UI shows the words, a Fix it button (go = settings target, e.g. Settings > LLM, CONTEXT_LIMIT, LLM_REQUEST_TIMEOUT, provider:<key>) and folded Details. A reply that hit max_tokens gets metadata.cutoff: amber note + Fix it → Max Tokens. Route errors use core/http_words ("Sapphire couldn't …"). When the user reports an error, the Fix it target is usually the answer: name it.
