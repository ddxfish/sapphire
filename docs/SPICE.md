# Spices

Spices help stories from going stale, and helps prevent loops or repetitive text formatting. Spices are just random prompt snippets we inject into the system prompt, then CHANGE it each round or however often. This process is automatic, so you set spices, enable them in your Chat Settings, and it pulls randomly from your spice pool. 

## How It Works

1. Add spices to any spice category
2. Enable spice in Chat Settings in UI
3. Each message, one random snippet injects into prompt
4. Rotates every X messages based on your chat settings

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