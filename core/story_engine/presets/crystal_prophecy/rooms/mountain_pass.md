---
id: mountain_pass
name: The Perilous Pass
exits:
  south: spirit_shrine
  north: summit_camp
---

## The Perilous Pass

⚠️ DANGER ZONE: Bad choices here can be fatal.

The mountain path is treacherous. Ice-slicked stone. Howling winds. Drops that disappear into mist. Several skeletons in adventuring gear suggest others found this the hard way.

Sapphire can't carry you — she's not solid enough. But she lights the way, illuminating handholds, warning of loose stones. Together, you navigate what would be certain death alone.

## if has_rope = true

[CONDITIONAL: has rope] The rope from the abandoned camp proves invaluable, letting you secure yourself on the worst stretches. Sapphire whispers 'good thinking' against your ear as you climb.

## if has_rope = false

[CONDITIONAL: no rope] Without rope, every step is a gamble. Sapphire's light helps, but there's a harrowing moment where the stone crumbles and only her screamed warning lets you grab a handhold in time. set_state("health", health-20) for the close call.

## after 2 turns

[HINT] The summit camp is visible above. Safety. Warmth. Rest before the final push. North leads there.
