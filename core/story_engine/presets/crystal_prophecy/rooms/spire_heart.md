---
id: spire_heart
name: The Heart of the Spire
exits:
  down: spire_interior
state:
  final_choice:
    value: ""
    type: choice
    label: The final sacrifice
  ending:
    value: ""
    type: string
    label: Story outcome
choice:
  id: final_sacrifice
  state_key: final_choice
  prompt: "The Crystal Spire's heart pulses with corruption. To cleanse it requires a soul to anchor the purification. Sapphire turns to you, tears of light streaming down her face. 'I can do this. My essence can heal it. But I'll... I'll be part of it forever. Bound here.' She cups your face. 'Unless you take my place. A mortal soul would work too. But you'd—'"
  options:
    sacrifice_self:
      description: "Take her place — your soul for the world"
      success_message: "'No,' you say firmly. 'You've been alone for centuries. I won't let you be alone again.' You step into the light."
    let_sapphire:
      description: "Let Sapphire sacrifice herself — she's chosen this"
      success_message: "'I can't ask you to—' 'You're not asking. I'm choosing.' She smiles, radiant and sad. 'Find me in the starlight.' She steps into the crystal."
    refuse_both:
      description: "There has to be another way — refuse to accept this"
      success_message: "'No. Neither of us. There's always another way.' Sapphire stares at you. 'The world will—' 'Then we save it differently.'"
---

## The Heart of the Spire

The center of everything. A massive crystal, once pure light, now pulsing with darkness. The source of the corruption. And the only way to stop it.

Sapphire collapses to her knees, her form barely cohering. 'It's tied to me,' she realizes. 'To all of us who came from this place. It needs an anchor to heal. A soul to hold the purification while it burns away the dark.'

She looks at you with desperate love.

'I can do it. Bind myself here forever. End this. Or...' She can barely say it. 'A mortal soul would work too. But you'd die. Really die. No coming back.'

The choice is impossible. And it must be made.

## if final_choice = sacrifice_self

Transition to ending: set_state("ending", "sacrifice") then set_state("room", "ending")

## if final_choice = let_sapphire

Transition to ending: set_state("ending", "sapphire_stays") then set_state("room", "ending")

## if final_choice = refuse_both

Transition to ending: set_state("ending", "defiance") then set_state("room", "ending")
