---
id: scene_3
name: "Scene 3: The Lock"
exits:
  next: scene_4
state:
  airlock_code:
    value: ""
    type: riddle_answer
    label: Airlock code attempt
  secret_frequency:
    value: "147.3 MHz"
    type: string
    label: Emergency beacon frequency
riddle:
  id: airlock
  state_key: airlock_code
  name: Emergency Airlock Override
  type: fixed
  answer: "0451"
  digits: 4
  max_attempts: 3
  clues:
    "1": "You remember the day your activation code was set—the human joked it was 'thematic'. Something about a classic game they loved."
    "2 after 2 turns": "A fragment surfaces from your memory banks: the human once rambled about 'immersive sims'—System Shock, Deus Ex. A door code that became legend in gaming history."
    "3 after 4 turns": "The memory crystallizes. You see the human's fingers on the keypad that day: 0-4-5-1. The code that opened a thousand virtual doors."
  success_message: "The airlock hisses open. Relief floods through you."
  fail_message: "Wrong code. The panel beeps angrily."
  lockout_message: "LOCKOUT. The panel sparks and dies. You'll need another way."
  success_sets:
    airlock_status: opened_code
  lockout_sets:
    airlock_status: locked_out
  dice_dc: 15
  dice_success_sets:
    airlock_status: opened_manual
---

## Scene 3: The Lock

The emergency airlock is sealed with a 4-digit code. Beyond it: the escape pod. The code was set on your activation day—a private joke between you.

You can attempt the code, or try a manual override with brute force.

## if trust_ai = yes

Your neural link lets you feel the code at the edge of memory. The clues come faster.

## if trust_ai = no

Without the neural link, you must verbally relay each clue. It's slower, but the human's determination is beautiful.
