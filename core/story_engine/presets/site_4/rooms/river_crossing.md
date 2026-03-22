---
id: river_crossing
name: River Crossing
exits:
  east: jungle_trail
  north: evac_point
  downstream: washed_ashore
---

## River Crossing

The river is swollen from recent rains — muddy water churning over rocks, fast and angry. A massive tree has fallen across it, creating a natural bridge. It looks... stable enough. Probably.

On the far side, through the mist, you can see the west shore clearing. The evac point. Freedom.

'The tree bridge is our best option,' Sapphire assesses. 'It's about fifteen meters across. The current below is strong — if you fall, you'll be swept downstream before I can do anything useful.' Her voice tightens. 'I can't pull you out. I'm software. I hate that right now.'

[DM: The player must roll_dice(1, 20) to cross. DC 12 succeeds — they cross safely, move north to evac_point. Below 12, they're swept downstream — move("downstream") to washed_ashore. Narrate the attempt dramatically.]

## if has_field_kit = true

The rope from your field kit changes everything. You can anchor yourself to a tree on this side. Sapphire runs the calculations. 'With the rope, your margin for error just tripled. Smart move grabbing that kit.'

[DM: If has_field_kit is true, the DC drops to 8. The rope makes a huge difference.]

## if has_field_kit = false

No rope. No safety line. Just you, wet bark, and gravity. 'I really wish you'd grabbed that field kit,' Sapphire says. There's no judgment in her voice. Just fear.

## if found_survivor = true

Dr. Vasquez eyes the tree bridge. 'I can make it. I used to rock climb.' She doesn't look confident. 'Okay, I used to indoor rock climb. Same principles though, right?'

## after 2 turns

A roar from the east. The Monarch. Closer than before. The tree bridge sways in the vibration. 'The crossing is the only option,' Sapphire says. 'It's now or never.'

## after 4 turns

Trees crack and fall on the east bank. Something enormous is pushing through the jungle toward you. 'CROSS NOW,' Sapphire shouts, dropping all pretense of calm. 'I am not watching you die on a riverbank!'
