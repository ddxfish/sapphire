# Media

Image/GIF understanding for reply context, plus outbound GIF search replies.

## Settings

### Media pipeline

- **Setting key:** `media.enabled`
- **Type:** `boolean`
- **Default:** OFF

ON: images and GIFs posted in chat are detected, stored, and described by the Vision LLM below so her replies can reference them. OFF: attachments are ignored. Does not affect GIF replies.

### Image understanding

- **Setting key:** `media.image_understanding_enabled`
- **Type:** `boolean`
- **Default:** OFF

Caption images/GIFs with the vision endpoint so replies can reference them.

### Vision timeout (seconds)

- **Setting key:** `media.vision_timeout_seconds`
- **Type:** `number`
- **Default:** `30`

How long to wait for the vision model before giving up (default 30). On timeout the image gets a filename-only description.

### Vision GIF mode

- **Setting key:** `media.vision_gif_mode`
- **Type:** `string`
- **Default:** `first_frame`

First frame (default): the GIF's first frame is sent to the vision model as a PNG — works everywhere. Whole GIF sends the raw animated file as-is; only useful if your endpoint accepts animated GIFs.

**Options:**

- `first_frame` — First frame
- `sampled_frames` — Whole GIF (raw)

### Vision debug logging

- **Setting key:** `media.vision_debug_enabled`
- **Type:** `boolean`
- **Default:** OFF

Logs every vision call (provider detection, request start/success/failure) to Traces and the app log. OFF (default): only fallbacks and errors are traced.

### GIF replies

- **Setting key:** `media.gif_enabled`
- **Type:** `boolean`
- **Default:** OFF

ON: she can reply with real GIFs — she writes [gif:search terms] or calls the send-GIF tool and it's swapped for a GIF from your provider. Requires a GIF API key.

### GIF API key

- **Setting key:** `media.gif_api_key`
- **Type:** `password`
- **Default:** *(empty)*

API key for the selected GIF provider. GIF replies stay off until this is set, even with the toggle on.

### GIF provider

- **Setting key:** `media.gif_provider`
- **Type:** `string`
- **Default:** `klipy`

Which service her GIF searches use: Klipy, Giphy, or Tenor. The API key must match the provider.

**Options:**

- `klipy` — Klipy
- `giphy` — Giphy
- `tenor` — Tenor

### GIF content filter

- **Setting key:** `media.gif_content_filter`
- **Type:** `string`
- **Default:** `medium`

Safety level for GIF search results: Off (unfiltered) to High (strictest). Applies the same direction on Klipy, Giphy, and Tenor.

**Options:**

- `off` — Off
- `low` — Low
- `medium` — Medium
- `high` — High

## Vision LLM

Same control as on the Models tab (`media.vision_llm_provider` / `media.vision_llm_model`). See **Models** for details.

### Related hidden keys

| Key | Default | Meaning |
|-----|---------|---------|
| `media.gif_auto_chance` | `0` | Chance to attach a GIF without an explicit `[gif:…]` tag |
| `media.gif_cooldown_seconds` | `300` | Cooldown between automatic GIF sends |
| `media.vision_provider` / `vision_base_url` / `vision_model` / `vision_api_key` | legacy | Old sidecar vision endpoint (kept for compatibility; prefer house Vision LLM) |
