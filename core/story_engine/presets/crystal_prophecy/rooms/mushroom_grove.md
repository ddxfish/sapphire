---
id: mushroom_grove
name: The Luminescent Grove
exits:
  east: forest_path
  north: fairy_ring
state:
  mushroom_choice:
    value: ""
    type: choice
    label: Mushroom decision
choice:
  id: mushroom_temptation
  state_key: mushroom_choice
  prompt: "A circle of luminescent mushrooms pulses invitingly. One glows gold — clearly magical. 'Those are Fool's Caps,' Sapphire warns. 'Eating one either grants visions... or grants you a very colorful death.'"
  options:
    eat:
      description: "Eat the golden mushroom — fortune favors the bold"
      success_message: "You pop it in your mouth. It tastes like regret and honey. The world melts into fractals. When reality returns, you KNOW things. Also, your tongue is blue now."
    refuse:
      description: "Decline — you've seen enough fantasy movies to know better"
      success_message: "'Smart,' Sapphire nods approvingly. 'The last person who ate one thought they could fly. They could not, in fact, fly.'"
---

## The Luminescent Grove

Giant mushrooms tower overhead, their caps pulsing with soft light. Spores drift like snow. It's breathtakingly beautiful and deeply wrong — the proportions are off, the colors too vivid.

'Welcome, welcome!' A tiny voice squeaks. A mushroom person, barely a foot tall, waddles toward you. 'Visitors! We love visitors! Stay forever!'

Sapphire's grip on your arm tightens. 'Politely. Decline. Everything.'

## if mushroom_choice = eat

[CONDITIONAL: ate mushroom] Your vision still swims occasionally with fractals. But you can see... more. Faint ley lines. Hidden paths. The mushroom folk regard you with new respect. 'One of us now. One of us.'

## after 3 turns

[HINT] The mushroom folk point north eagerly. 'Fairy ring! Fairy ring! Friends there! They love visitors even MORE than us!' This is somehow not reassuring.
