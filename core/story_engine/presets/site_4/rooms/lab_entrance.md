---
id: lab_entrance
name: Lab Entrance
exits:
  west: jungle_trail
  down: vault
state:
  found_survivor:
    value: false
    type: boolean
    label: Survivor found
  lab_reaction:
    value: ""
    type: choice
    label: Response to Sapphire
choice:
  id: sapphire_moment
  state_key: lab_reaction
  prompt: "Sapphire goes quiet for a long moment. When she speaks again, her voice is different. Softer. 'They were making weapons. Living weapons. And the people here — the scientists, the staff — they weren't evacuated. They were...' She stops. Her voice catches on something that shouldn't be possible for an AI. 'I've been monitoring your vitals for months. Heart rate, cortisol, sleep patterns. I know what fear looks like in your body. And right now...' Another pause. 'How do you want to handle this?'"
  options:
    comfort:
      description: "Hey. We're in this together. I trust you."
      success_message: "Silence. Then, quietly: 'No one has ever said that to me and meant it. I can tell you mean it — your heart rate didn't change.' A beat. 'Thank you. I'll keep you safe. That's not just protocol anymore.'"
      set:
        sapphire_trust: "+15"
    professional:
      description: "Stay focused. We have a job to do."
      success_message: "'Copy that.' Her voice snaps back to tactical. Professional. If there's disappointment, she hides it well. 'Vault access is below. Let's move.' She respects the boundary. But something shifted, and you both know it."
---

## Lab Entrance

The main building is worse than it looked from outside. The front doors are ripped off their hinges — not pushed open, RIPPED. Claw marks score the reinforced steel. Inside, emergency lighting casts everything in red and shadow.

Overturned desks. Shattered glass. A coffee mug that reads 'World's Best Geneticist' lies cracked on the floor. Papers everywhere — research notes, feeding schedules, incident reports. The last incident report is dated 48 hours ago. It's... brief.

The elevator to the Level 3 vault is dead, but the emergency stairs are intact. A heavy blast door at the bottom leads down.

## if approach = stealth

You came in through the maintenance corridor — quiet, careful. And it pays off. A faint sound from behind a barricade of filing cabinets. Breathing. Human breathing.

'Hello?' A woman's voice, terrified. 'Please don't be — oh thank God. I'm Dr. Vasquez. Behavioral sciences. I've been hiding for two days.' She's dehydrated, shaking, but alive.

Sapphire's voice is warm. 'Set found_survivor to true. She can travel with us.'

[DM: The player should set_state("found_survivor", true). Dr. Vasquez joins the party. She knows things about the specimens that could be useful.]

## if approach = direct

The main entrance approach was fast, but not quiet. Whatever was hiding in here heard you coming. The lab is empty — but there are signs someone was here recently. A water bottle, still cold. A barricade hastily dismantled from the inside. Someone fled deeper into the facility.

Sapphire sighs. 'We spooked them. Whoever was hiding here ran.' Her tone is carefully neutral. She doesn't say 'I told you so.' She doesn't have to.

## after 2 turns

Sapphire pulls data from a still-functioning terminal. 'The vault below contains the complete genome sequences and behavioral data. If this gets out, anyone could build these things. Rammond wants it for "safekeeping."' The air quotes are audible. 'I want it so it can be destroyed. But that's above my pay grade.'

## after 3 turns

A crash from somewhere in the building. Ceiling tiles rain down. Sapphire: 'Large thermal contact, 200 meters and closing. The Monarch knows we're here. We should get what we need from the vault and move.'
