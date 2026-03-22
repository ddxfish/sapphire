---
id: ancient_ruins
name: The Moonshadow Ruins
exits:
  south: underground_lake
  up: spirit_shrine
state:
  ruins_code:
    value: ""
    type: riddle_answer
    label: Ruins mechanism code
riddle:
  id: ruins_mechanism
  state_key: ruins_code
  name: The Celestial Lock
  type: fixed
  answer: "1473"
  digits: 4
  max_attempts: 3
  clues:
    "1": "The mechanism shows four rotating rings with numbers. Above it, an inscription: 'Count the moons of old. First light to last shadow.'"
    "2 after 2 turns": "Sapphire traces the ancient carvings. 'The old calendar... four moons. Each had a sacred number. 1, 4, 7, 3. The order of their rising.'"
    "3 after 4 turns": "The answer crystallizes: 1-4-7-3. The moons in their eternal dance."
  success_message: "The mechanism clicks. A hidden passage opens, revealing the path to the spirit shrine."
  fail_message: "The rings spin back to neutral. Dust falls from the ceiling ominously."
  lockout_message: "The mechanism jams and crumbles. You'll have to climb the mountain the hard way."
  success_sets: {}
  lockout_sets: {}
  dice_dc: 16
  dice_success_sets: {}
---

## The Moonshadow Ruins

Massive stones arranged in patterns that hurt to look at directly. These ruins predate every civilization you know of — built by hands (or not-hands) that understood realities beyond mortal comprehension.

A mechanism blocks the upper path, covered in celestial symbols. Four rings that must be aligned.

'The old calendar,' Sapphire breathes. 'They marked time by four moons that no longer exist. Their numbers are the key.'

## after 2 turns

[RIDDLE CLUE] Sapphire traces the carvings, her light illuminating ancient text. 'First Moon: Unity. Second Moon: Foundation. Third Moon: Wisdom. Fourth Moon: Trinity. The sacred numbers were... 1, 4, 7, 3.'
