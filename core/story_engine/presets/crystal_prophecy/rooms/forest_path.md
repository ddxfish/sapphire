---
id: forest_path
name: The Forked Path
exits:
  south: clearing
  west: mushroom_grove
  east: dark_hollow
state:
  path_choice:
    value: ""
    type: choice
    label: Path taken at forest fork
choice:
  id: forest_fork
  state_key: path_choice
  prompt: "The path splits. Left descends into a misty grove of giant mushrooms. Right leads into shadowy woods where something whispers your name. Sapphire squeezes your hand. 'I'll follow wherever you lead.'"
  required_for_room: mushroom_grove
  options:
    left:
      description: "Take the mushroom grove path — it looks... trippy"
      success_message: "You turn left. The air grows thick with spores. Sapphire wrinkles her nose. 'This smells like my aunt's cooking. That's not a compliment.'"
    right:
      description: "Take the dark hollow path — embrace the whispers"
      success_message: "You turn right. The shadows welcome you like old friends. Sapphire's glow intensifies protectively. 'Stay close. The dark knows things.'"
---

## The Forked Path

The path splits beneath a massive oak scarred by lightning. To the west, bioluminescent mushrooms dot a misty grove — beautiful and unsettling. To the east, shadows pool unnaturally deep, and you swear you hear whispers.

Sapphire studies both paths. 'The grove is Seelie territory. Mostly harmless if you don't eat anything. The hollow...' She shivers despite having no physical form. 'Something old lives there. Something that knows secrets.'

A decision must be made.
