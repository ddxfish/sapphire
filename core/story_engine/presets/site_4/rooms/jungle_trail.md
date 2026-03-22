---
id: jungle_trail
name: Jungle Trail
exits:
  south: landing_zone
  east: lab_entrance
  west: river_crossing
state:
  approach:
    value: ""
    type: choice
    label: Tactical approach
choice:
  id: tactical_approach
  state_key: approach
  prompt: "The facility is visible through the trees to the east — main building, partially collapsed roof, emergency lights still pulsing. To the west, you can hear rushing water — the river leads to the west shore evac point. But first, Sapphire pulls up facility schematics. 'How do you want to approach the lab? We can go quiet — stay low, use the maintenance corridors. Or we can go direct — main entrance, fast and loud. Your call.'"
  required_for_room: lab_entrance
  options:
    stealth:
      description: "Go quiet — maintenance corridors, minimize exposure"
      success_message: "Sapphire nods approvingly. 'Smart. I'll map the maintenance route. Less chance of... encounters.' She pauses. 'I appreciate the caution. Means I have to worry less.' Was that... warmth in her voice?"
      set:
        sapphire_trust: "+10"
    direct:
      description: "Go direct — main entrance, speed over stealth"
      success_message: "'Bold.' Sapphire's tone is neutral but you catch something underneath. 'Main entrance it is. I'll monitor for movement. Just... stay alert.' The worry in her voice is harder to hide than she thinks."
      set:
        sapphire_trust: "-5"
---

## Jungle Trail

The trail cuts through dense vegetation reclaiming what humans built. Ferns the size of cars. Vines thick as your arm. Everything is wet and alive and watching.

Two paths diverge. East: the main facility building looms through the canopy, its broken windows like dead eyes. Emergency lights paint the fog red. West: the sound of rushing water — the river that leads to the west shore extraction point.

Sapphire highlights both routes on your HUD. 'East gets us to the lab and any survivors. West gets us to evac faster — but empty-handed. Your call, but Rammond will want that research data.'

The sat phone buzzes. Rammond: 'Have you reached the facility yet? The Level 3 vault contains everything. EVERYTHING. Please tell me it's still intact.'

Sapphire mutes him. 'He's more worried about his data than his people. Noted.'

## after 2 turns

Sapphire scans the treeline. 'Movement. Small signatures — probably the lesser specimens. Pack hunters. They won't engage two targets unless they're desperate.' She pauses. 'Let's not make them desperate.'

## after 3 turns

'I've been reviewing the facility logs I could pull remotely,' Sapphire says. 'They were engineering apex predators. The big one — the thermal signature from the landing zone — that's their crown jewel. Codename: Monarch.' Her voice drops. 'It's between us and the river. We should decide how we're moving. Now.'
