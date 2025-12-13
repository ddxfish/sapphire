# Toolsets

Named groups of tools. Switch what abilities the AI has access to per-chat.

---

## Usage

Use the UI to edit tool sets. Select a toolset in chat settings or from the pill dropdown. AI only sees tools in that group.

---

## Example

```json
{
  "default": {
    "functions": ["get_memories", "search_memory"]
  },
  "work": {
    "functions": ["web_search", "get_website", "get_wikipedia"]
  },
  "minimal": {
    "functions": []
  }
}
```

---

## Files

| File | Purpose |
|------|---------|
| `core/modules/system/toolsets/toolsets.json` | Default toolsets |
| `user/toolsets/toolsets.json` | Your custom toolsets |

User file overrides defaults.