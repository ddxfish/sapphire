# Prompts

Two types: **Monolith** (single block) and **Assembled** (component pieces). Sapphire shines with Assembled prompts, it's this app's special feature when you want the AI to edit its own prompt, like adding/removing emotions dynamically. 

## Variables used in prompts

- `{ai_name}` - Change in Settings
- `{user_name}` - Change in Settings


## Monolith

One complete prompt string. Simple, direct. Use assembled prompts if you want Sapphire's full capabilities.

```
You are {ai_name}. You help {user_name} with tasks. Be concise.
```

## Assembled (recommended)

Prompt built from swappable pieces. Mix and match. Ideal for stories where the AI needs to swap locations, or companions swapping emotions themselves, or just seeing what custom weird prompts your AI cooks up. This is Sapphire's unique ability.

### Sections

| Section | Purpose |
|---------|---------|
| persona | Who the AI is |
| location | Setting/environment |
| relationship | How AI relates to user |
| goals | What AI should do |
| format | Response style |
| scenario | Current situation |
| extras | Additional rules (multiple allowed) |
| emotions | Current mood (multiple allowed) |
