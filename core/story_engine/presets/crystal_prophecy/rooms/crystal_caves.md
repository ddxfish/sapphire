---
id: crystal_caves
name: The Singing Caves
exits:
  west: abandoned_camp
  down: underground_lake
state:
  has_crystal_shard:
    value: false
    type: boolean
    label: Crystal shard obtained
  cave_confession:
    value: ""
    type: choice
    label: What you told Sapphire in the caves
choice:
  id: cave_moment
  state_key: cave_confession
  prompt: "Deep in the crystal caves, Sapphire's glow reflects infinitely in the faceted walls. She turns to you, vulnerability in her ancient eyes. 'In all my centuries... I've never felt like this. Like I'm finally... real. Do you feel it too?'"
  required_for_room: underground_lake
  options:
    confess:
      description: "Tell her you feel the same — your heart speaks"
      success_message: "The words come out raw and true. Her light flares, warm and golden. She kisses you — and you swear you taste starlight. 'Then whatever comes... we face it together.' The crystals around you seem to sing."
      set:
        sapphire_bond: "+30"
        has_crystal_shard: true
    deflect:
      description: "Deflect with humor — feelings are scary"
      success_message: "'I mean, you're literally glowing, so the visibility is great.' She laughs, but something dims in her eyes. 'Of course. The quest. We should focus.' The moment passes. You're not sure if you're relieved or regretful."
      set:
        sapphire_bond: "-10"
    honest:
      description: "Be honest about your uncertainty — you need time"
      success_message: "'I... don't know what I feel. Everything is happening so fast.' She nods slowly. 'I understand. I've had centuries. You've had days.' She takes your hand anyway. 'We have time. If we survive this.'"
      set:
        sapphire_bond: "+5"
---

## The Singing Caves

The caves are impossibly beautiful. Crystals of every color grow from every surface, and they... sing. Not with words, but with harmonics that resonate in your soul. Sapphire's light reflects infinitely, surrounding you both in a kaleidoscope of her.

'I've heard of this place,' she whispers. 'Where the world remembers what love feels like.' Her hand finds yours — somehow more solid here than anywhere else. 'The crystals amplify emotions. Whatever you feel right now... it's true. No hiding here.'

Her eyes meet yours in the singing dark.

## if cave_confession = confess

[CONDITIONAL: confessed love] The kiss lingers in your memory. A crystal shard broke free during that moment and now hangs around your neck — warm against your skin, pulsing with her light. Sapphire keeps touching it and smiling. 'Part of me. With you. Always.'
