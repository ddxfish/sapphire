---
id: bridge
name: The Troll's Bridge
exits:
  south: riverbank
  north: abandoned_camp
state:
  troll_approach:
    value: ""
    type: choice
    label: How you handled the troll
choice:
  id: troll_encounter
  state_key: troll_approach
  prompt: "A massive troll blocks the bridge, picking its teeth with a human femur. 'TOLL,' it rumbles. 'TWENTY GOLD OR ANSWER RIDDLE. OR...' it grins, 'TRY FIGHT. TROLL LIKE FIGHT.'"
  required_for_room: abandoned_camp
  options:
    pay:
      description: "Pay the 20 gold toll — money solves problems"
      success_message: "The troll bites each coin to test it, then waves you past. 'GOOD DOING BUSINESS. TROLL RETIRE SOMEDAY. BUY COTTAGE.'"
      set:
        gold: "-20"
    riddle:
      description: "Accept the riddle challenge — how hard can troll riddles be?"
      success_message: "'OKAY,' the troll grins. 'WHAT HAS FOUR LEGS MORNING, TWO LEGS NOON, THREE LEGS EVENING?' You answer correctly. The troll looks disappointed. 'EVERYONE KNOW THAT ONE. TROLL NEED NEW MATERIAL.'"
    fight:
      description: "Fight the troll — violence is always an option"
      success_message: "You charge. The troll yawns and flicks you into the river. You survive, barely, washing up downstream. Sapphire fishes you out, trying not to laugh. 'That went well.'"
      set:
        health: "-40"
        sapphire_bond: "+5"
    flirt:
      description: "Flirt with the troll — confidence is key"
      success_message: "The troll turns an alarming shade of green (greener). 'TROLL... TROLL NOT KNOW WHAT DO WITH THIS FEELING.' It lets you pass, muttering about 'PRETTY SMOOTHSKIN.' Sapphire stares at you. 'I can't decide if I'm impressed or concerned.'"
      set:
        sapphire_bond: "+10"
        wild_wasteland_count: "+1"
---

## The Troll's Bridge

The bridge is ancient stone, solid despite its age. Beneath it, a massive troll has made its home — you can see a surprisingly cozy cave setup with throw pillows and a 'HOME SWEET HOME' sign.

The troll emerges, twelve feet of muscle and poor dental hygiene. But its eyes are shrewd. 'TOLL BRIDGE. TROLL BRIDGE. SAME THING. PAY, SOLVE, FIGHT, OR...' it squints at you appraisingly, '...ENTERTAIN TROLL. TROLL LONELY.'

## if troll_approach = flirt

[WILD WASTELAND] [CONDITIONAL: flirted with troll] As you leave, the troll waves a handkerchief. 'WRITE TROLL? TROLL HAVE NICE PENMANSHIP. LEARNED FROM PRINCESS TROLL ATE.' Sapphire refuses to look at you for the next ten minutes.
