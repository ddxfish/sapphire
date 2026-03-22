---
id: landing_zone
name: Landing Zone
exits:
  north: jungle_trail
state:
  has_field_kit:
    value: false
    type: boolean
    label: Field kit acquired
---

## Landing Zone

The helicopter banks hard and drops you on a concrete pad cracked by roots and time. Before you can blink, it's climbing away — fast. The pilot didn't even cut engines.

Red emergency lights spin lazily on rusted poles. The klaxons died hours ago but nobody turned off the lights. Rain drips from everything. The air smells like ozone, wet concrete, and something organic you'd rather not identify.

Your sat phone crackles. 'Sapphire, {user_name}, this is Rammond.' The voice is too calm. 'The facility went dark forty-eight hours ago. We've lost contact with all on-site personnel. Your objectives are simple — locate any survivors, secure the Level 3 research vault, and get to the west shore evac point. The extraction bird arrives in...' a pause, '...when it arrives.'

Sapphire's voice comes through your earpiece, cool and precise. 'I'm reading residual thermal signatures scattered across the facility. Some of them are very large. There's a field kit in the supply cache by the pad — I'd recommend grabbing it before we move.'

A distant roar shakes the canopy. Something big. Something hungry.

'That would be one of the specimens,' Sapphire says. 'Moving north into the jungle is our only path forward.'

[DM: The player can pick up the field kit with set_state("has_field_kit", true). It contains rope, a flare gun, and emergency supplies. Mention it's available but don't force them to take it.]

## after 2 turns

Another roar. Closer this time. The red lights flicker. Sapphire's tone sharpens: 'The thermal signature is moving. We should go. North. Now.'

## after 4 turns

The ground trembles. Trees at the jungle edge sway — pushed aside by something massive. 'Respectfully,' Sapphire says, 'we are out of time to sightsee. North. Please.'
