---
id: death
name: Game Over
type: death
respawn: clearing
respawn_set:
  health: 100
state:
  death_cause:
    value: ""
    type: string
    label: How you died
---

## Game Over

[DEATH] Something went terribly wrong. The adventure ends here... but not THE story. Use move("respawn") to return and try again.

## if death_cause = mushroom

[DEATH: MUSHROOM]

The golden mushroom's effects intensify. Colors become sounds. Sounds become smells. Reality itself becomes optional. In your hallucinatory wisdom, you decide you can definitely fly.

You cannot, in fact, fly.

Sapphire finds your body at the bottom of a ravine, surrounded by beautiful flowers that grew from the magic in your blood. 'You absolute idiot,' she whispers, and her tears water the flowers.

*Try again. Maybe don't eat random fungi this time.*

## if death_cause = frog_court

[WILD WASTELAND DEATH: FROG COURT]

You laughed at their judicial proceedings. You mocked their tiny wigs. You questioned the legitimacy of amphibian taxation.

The Enforcement Mechanism turned out to be a surprisingly large and legally authorized toad with a taste for contempt-of-court violators.

Sapphire watches in horror as justice is served. 'I TOLD you to just go with it,' she says to your remains.

*Try again. Respect the frog legal system.*

## if death_cause = pass_fall

[DEATH: MOUNTAIN PASS]

Without rope, without proper preparation, the mountain claims another victim. The fall is long. You have time to regret every decision that led here.

Sapphire tries to catch you, but she's not solid enough. She screams your name all the way down. She'll scream it for centuries, in her dreams, when she has them.

*Try again. Next time, pack rope.*
