# Spices

Random text snippets injected into system prompts. Rotates each message. The spice must flow. This is really for stories, and could negatively impact other uses like research. 

---

## Purpose

- Break repetitive response patterns
- Add variety without raising temperature
- Keep stories coherent while avoiding loops
- Safer creativity control than temp slider

---

## How It Works

1. Add spices to any spice category
2. Enable spice in chat settings in UI
3. Each message, one random snippet injects into prompt
4. Rotates every X messages based on your chat settings

---

## Example Pool

```json
{
  "storytelling": [
    "Something unexpected is about to happen.",
    "A minor character becomes important.",
    "The weather shifts dramatically.",
    "An old memory surfaces.",
    "Someone is not who they seem."
  ]
}
```

---

## Built-in Pools

Note, I didn't ever use categories, they are for human eyes, any category works. It's all just that spice file with categories that get collapsed on read.

| Pool | Use |
|------|-----|
| `action` | Tension and stakes |

---

## Files

| File | Purpose |
|------|---------|
| `prompt_spices.json` | Default spice pools (don't edit) |
| `user/prompt_spices.json` | Your custom pools (overrides) |

---

## Notes

- Keep snippets vague enough to fit any scene
- Short phrases work better than sentences