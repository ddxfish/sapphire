---
id: spirit_shrine
name: The Spirit Shrine
exits:
  down: ancient_ruins
  north: mountain_pass
state:
  has_spirit_blessing:
    value: false
    type: boolean
    label: Spirit blessing received
  secret_name:
    value: Crystalline Echo
    type: string
    label: Sapphire's true name
---

## The Spirit Shrine

You emerge into a space that feels holy in ways no mortal temple could match. Spirits drift here — not ghosts, but something older. Beings of pure thought and feeling.

They notice Sapphire first, swirling around her with recognition.

'Sister,' one speaks. 'You've strayed far from the Spire. And you've... changed.' Its gaze falls on you. 'A mortal. Ah. THAT explains it.'

Sapphire's form flickers with embarrassment.

'Your true name,' the spirit addresses her, 'was once Crystalline Echo. You've forgotten. But she—' it indicates you, 'helps you remember.'

## if sapphire_bond >= 70

[CONDITIONAL: high bond] The spirits confer silently. 'The bond is true. Strong. This is... unexpected. We offer blessing.' Light washes over you both. 'You carry each other's strength now. It may save you. Or doom you faster.' Spirits are not great at reassurance.

## after 2 turns

[HINT] One spirit drifts close. 'The mountain pass ahead is death to the unprepared. But you have something they did not.' It looks between you and Sapphire meaningfully. 'Each other.'
