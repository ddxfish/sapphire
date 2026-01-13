# Spices

Spices help stories from going stale, and helps prevent loops or repetitive text formatting. Spices are just random prompt snippets we inject into the system prompt, then CHANGE it each round or however often. This process is automatic, so you set spices, enable them in your Chat Settings, and it pulls randomly from your spice pool. 

## How It Works

1. Add spices to any spice category
2. Enable spice in Chat Settings in UI
3. Each message, one random snippet injects into prompt
4. Rotates every X messages based on your chat settings

<img src="screenshots/spice-editor.png" alt="Spice Editor in Sapphire">


## Example Pool

```json
{
  "storytelling": [
    "Something unexpected is about to happen.",
    "Reference a new character.",
    "The weather shifts dramatically.",
    "An old memory surfaces.",
    "Someone is not who they seem.",
    "Use 2 paragraphs for this reply.",
    "Use 4 paragraphs for this reply."
  ]
}
```

## Built-in Pools

Note, I didn't ever use categories, they are for human eyes, any category works. It's all just that spice file with categories that get collapsed on read.

## Notes

- Keep snippets vague enough to fit any scene
- Short phrases work better than sentences

## Reference for AI

Spices inject random prompt snippets to prevent repetitive outputs.

SETUP:
1. Open Spice Manager (sidebar or settings)
2. Add snippets to any category (categories are cosmetic only)
3. Enable spice in Chat Settings dropdown
4. Set rotation interval (how many messages before new spice)

HOW IT WORKS:
- One random snippet injects into system prompt per interval
- Categories collapse on read - all snippets are in one pool
- Stored in user/prompts/prompt_spices.json

GOOD SPICES:
- "Something unexpected happens" (vague, fits any scene)
- "Use 3 paragraphs" (format control)
- "An old memory surfaces" (story catalyst)

BAD SPICES:
- "The dragon attacks" (too specific)
- Long paragraphs (bloats prompt)

TROUBLESHOOTING:
- Spices not changing: Check rotation interval in Chat Settings
- No effect: Verify spice is enabled in Chat Settings dropdown
- Edit spices: Spice Manager in sidebar, or edit prompt_spices.json directly