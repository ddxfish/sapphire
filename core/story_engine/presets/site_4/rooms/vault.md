---
id: vault
name: Research Vault
exits:
  up: lab_entrance
state:
  vault_code:
    value: ""
    type: riddle_answer
    label: Vault access code
  has_research_data:
    value: false
    type: boolean
    label: Research data secured
riddle:
  id: facility_code
  state_key: vault_code
  name: Facility Override Code
  type: fixed
  answer: "1993"
  digits: 4
  max_attempts: 3
  clues:
    "1": "The keypad display reads: 'SITE 4 — FACILITY OVERRIDE. Enter authorization code.' Four digits. A faded label below the keypad reads 'Hint: Genesis.' Sapphire scans the door. 'Reinforced titanium. We're not getting through without the code... or a very good roll.'"
    "2 after 2 turns": "Sapphire pulls a maintenance log from a wall terminal. 'Found something. Site 4 Bioengineering Division, established nineteen—' The screen flickers and dies. 'Damn. The founding year. That's our code. I need more data to narrow it down.'"
    "3 after 4 turns": "A torn page from a logbook on the floor catches your eye. The handwriting is frantic: 'March 15th. Three years since we broke ground in '93. The specimens are growing faster than projected. God help us all.' Sapphire: 'Ninety-three. The founding year. 1-9-9-3.'"
  success_message: "The vault door hisses and swings open. Banks of servers line the walls, cold and humming on backup power. Everything is here — every genome, every experiment, every failure. Sapphire immediately begins the data transfer."
  fail_message: "The keypad buzzes red. Wrong code. The display flashes a warning."
  lockout_message: "LOCKOUT ENGAGED. The keypad goes dark. 'That's it for the keypad,' Sapphire says tightly. 'But the door mechanism has a manual release. We could try to force it — it'll take muscle, not brains.'"
  success_sets:
    has_research_data: true
  lockout_sets: {}
  dice_dc: 16
  dice_success_sets:
    has_research_data: true
---

## Research Vault

The stairs lead down to a heavy blast door. The air is colder here — the vault has its own climate system, still running on backup generators. A keypad glows faintly beside the door.

'Level 3,' Sapphire confirms. 'This is what Rammond wants. Full genome library, behavioral programming data, the works. If these sequences got out, anyone with a lab and ambition could build an army of these things.'

The sat phone crackles. Rammond: 'Are you at the vault? The code — I can't remember if it's — it was the year we — just try the code. It's the founding year.'

Sapphire: 'Helpful as always, Doctor.'

[DM: The player needs to crack the 4-digit code. They can attempt answers with set_state("vault_code", "XXXX", "reason"), or roll_dice(1, 20) to brute-force the manual release (DC 16). If they succeed either way, set has_research_data to true. The clues progress over turns.]

## after 2 turns

The building shudders above you. Dust falls from the ceiling. Sapphire: 'The Monarch is in the building. We need to work faster.'

## after 5 turns

A deafening impact from above. The lights flicker. 'It's directly above us,' Sapphire whispers. 'The vault door is the only thing between us and it. If we can't open it from this side, at least it can't open it from that side. Small mercies.'
