# Cognition

Human world-model social judgment: channel situation, relationship-aware timing, and optional intention competition. Complements [Memory](Memory.md) (people facts) and [Conversation](Conversation.md) (reply modes / chances).

See also [human world-model roadmap](../human_world_model_roadmap.md).

## Settings

### Relationship-aware social timing

- **Setting key:** `profile.relationship_policy_enabled`
- **Type:** `boolean`
- **Default:** ON

ON (default): familiarity and fondness softly scale organic replies, reactions, and outreach. Hard cooldowns and safety gates still win.

### Relationship policy strength

- **Setting key:** `profile.relationship_policy_strength`
- **Type:** `string`
- **Default:** `normal`

How strongly relationship scores reshape social chances.

**Options:** `subtle` · `normal` · `bold`

### Channel situation model

- **Setting key:** `cognitive.situation_enabled`
- **Type:** `boolean`
- **Default:** ON

Build a short-lived channel vibe snapshot (quiet / calm / lively / playful / heated) from recent messages for prompts and gates (organic chance, outreach skip when heated/busy).

### Inject situation into prompts

- **Setting key:** `cognitive.situation_in_prompt`
- **Type:** `boolean`
- **Default:** ON

When situation is on, add a compact channel-situation block to reply instructions (data fence, not instructions).

### Intention competition (reply / react / silent)

- **Setting key:** `cognitive.intention_competition_enabled`
- **Type:** `boolean`
- **Default:** OFF

OFF (default): legacy organic % roll, then optional silent react on miss. ON: score reply vs react-only vs silence from situation + relationship (mentions/DMs still always reply). May schedule a deferred `social_check_in` world-model task when she stays quiet with a known person.

## Debugging

Open the **Debug** tab → **Cognition preview**. It shows live flags, last per-channel situations, intention winners, and gate multipliers (also `GET debug/cognition`). Reload the Discord plugin/daemon after pulling this build so the panel and API are registered.

### LLM debug ring

- **Setting key:** `cognitive.llm_debug_enabled`
- **Type:** `boolean`
- **Default:** OFF

Keeps the last 10 prompts and replies in memory for the Debug panel — full prompts, other people's messages included. Off = nothing retained. The panel's Clear button (`POST debug/clear`) empties it.

### Side lanes: local providers only

- **Setting key:** `cognitive.side_lanes_local_only`
- **Type:** `boolean`
- **Default:** ON

Greetings, goodnights, ambient distill and image captions on `auto` pick only providers marked local in Settings › LLM, so server chatter never rides to a cloud model by accident. OFF: any provider in the fallback order. An explicit provider pick (Reply LLM, greeting model, vision model) always wins.
