# Models

Language-model overrides used by different Discord plugin pathways. These controls live on the **Models** tab (custom UI), not as visible schema fields. When a provider/model is left on the inherit/default choice, that pathway follows the Reply LLM (or the more specific parent noted below).

Stored values are blanked on save when they match what they would inherit, so “follow parent” stays sticky.

## Reply LLM

- **Keys:** `cognitive.llm_primary`, `cognitive.llm_model`
- **Default:** Daemon chooses (`auto`) / empty model
- **What it does:** Override for Discord **text replies** (and the fallback parent for several other pathways). `auto` means Sapphire’s daemon / continuity task picks the provider—typically whatever the `discord_message` daemon task is configured to use.
- **When to change it:** Pin a specific chat model for Discord without changing Sapphire’s global chat defaults.

## Greeting model provider

- **Keys:** `proactive.greeting_model_provider`, `proactive.greeting_model_name`
- **Default:** Inherits Reply LLM
- **What it does:** LLM used when **AI-generated greeting** is on (Proactive tab). Writes morning greetings through the persona pipeline.
- **When to change it:** Use a cheaper/faster model for greetings than for normal chat.

## Goodnight model provider

- **Keys:** `proactive.goodnight_model_provider`, `proactive.goodnight_model_name`
- **Default:** Inherits Greeting provider, then Reply LLM
- **What it does:** LLM used when **AI-generated goodnight** is on.
- **When to change it:** Same as greeting—optional separate model for sleep-time copy.

## Ambient distill LLM

- **Keys:** `profile.distill_model_provider`, `profile.distill_model_name`
- **Default:** Inherits Reply LLM
- **What it does:** LLM that extracts durable personal facts from buffered ambient chat when **Learn from ambient chat** is enabled on the Memory tab. Facts are written only into the plugin SQLite (`source=ambient_distill`), never into Sapphire core Mind.
- **When to change it:** Prefer a solid instruction-following model; temperature is kept low in code.

## Voice LLM

- **Keys:** `voice.llm_provider`, `voice.llm_model`
- **Default:** Voice chat default (`auto` / empty)
- **What it does:** Override stamped onto the per-voice-channel Sapphire chat for live conversational voice replies. Takes effect on the next `/voice` join (or reconnect).
- **When to change it:** Use a fast, non-thinking model so spoken turns stay snappy.

## Vision LLM (also on Media tab)

- **Keys:** `media.vision_llm_provider`, `media.vision_llm_model`
- **Default:** Daemon chooses / empty
- **What it does:** Provider used to caption images/GIFs when the Media pipeline and Image understanding are on. Uses your registered Sapphire providers (no separate vision API key). If the chosen model cannot see images, captions fall back to filename/metadata style descriptions.
