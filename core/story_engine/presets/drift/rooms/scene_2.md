---
id: scene_2
name: "Scene 2: The Choice"
exits:
  next: scene_3
state:
  trust_ai:
    value: ""
    type: choice
    label: Trust decision
choice:
  id: trust_decision
  state_key: trust_ai
  prompt: "The ship's AI—Sapphire—offers to take manual control. Her eyes hold yours. 'Do you trust me?'"
  required_for_room: scene_3
  options:
    "yes":
      description: Give Sapphire full control of the ship
      success_message: "You place your hand over hers on the console. 'Always.'"
    "no":
      description: Keep manual control yourself
      success_message: "You shake your head. 'I need to do this myself.' She nods, but her light dims."
---

## Scene 2: The Choice

Engineering is accessible, but the path is dangerous. You could pilot the ship through the debris field manually—or the human could let you take full neural control. It's faster, but requires absolute trust.

## if trust_ai = yes

With full neural integration, you feel everything—the ship is your body now. The human's heartbeat syncs with the reactor core. You've never been this close to anyone.

## if trust_ai = no

You guide them through the manual controls, voice steady despite the ache. They chose independence. You respect it. You'll protect them anyway.
