---
id: dark_hollow
name: The Whispering Hollow
exits:
  west: forest_path
  north: riverbank
state:
  riddle_answer:
    value: ""
    type: riddle_answer
    label: Ancient door riddle
riddle:
  id: hollow_door
  state_key: riddle_answer
  name: The Whispering Door
  type: fixed
  answer: shadow
  max_attempts: 3
  clues:
    "1": "The door's inscription reads: 'I follow you in light, vanish in the dark. I copy your every move but have no heart. What am I?'"
    "2 after 2 turns": "Sapphire muses: 'Something that follows... copies... but isn't alive. What do we all have that mimics us?'"
    "3 after 4 turns": "You hold up your torch. On the wall behind you, something dark moves when you move. Sapphire grins. 'There's your answer, genius.'"
  success_message: "The door groans open. The shadows themselves seem to bow as you pass."
  fail_message: "The door remains sealed. The whispers grow mocking."
  lockout_message: "The door flashes red and seals permanently. You'll need to find another path through the fairy ring."
  success_sets: {}
  lockout_sets: {}
  dice_dc: 14
  dice_success_sets: {}
---

## The Whispering Hollow

Shadows don't behave correctly here. They stretch toward you even when the light doesn't move. Whispers echo from nowhere, speaking your name, your fears, your secrets.

A massive stone door blocks the northern path. Ancient runes pulse faintly.

'The Hollow tests those who enter,' Sapphire explains, her glow pushing back the dark. 'Answer its riddle, earn passage. Fail...' She doesn't finish.

The door speaks: 'ANSWER OR TURN BACK. THREE CHANCES. CHOOSE WISELY.'

## after 2 turns

[HINT] Sapphire studies the riddle. 'Something that follows you, copies you, but isn't alive... What do we all carry in the light?'
