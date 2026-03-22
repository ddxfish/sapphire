---
id: evac_point
name: Evac Point
---

## Evac Point

You made it. The west shore clearing opens up before you — flat ground, tall grass, a faded windsock hanging limp. The ocean stretches to the horizon. Somewhere out there, a helicopter is coming.

Sapphire lets out a breath she doesn't technically need. 'We're clear of the jungle canopy. I'm broadcasting the extraction beacon. ETA...' She checks. '...twelve minutes.'

Twelve minutes. You sit down in the grass. Behind you, the jungle. The island. Everything that happened in there.

The sat phone rings one last time. Rammond.

## if has_research_data = true, found_survivor = true

'You got the data AND a survivor? Both? I—' Rammond's voice breaks. 'Dr. Vasquez is alive? Oh thank God. Get her home. Get it all home.' For the first time, he sounds like a person instead of a bureaucrat.

Dr. Vasquez sits beside you in the grass, still shaking but alive. 'Thank you,' she says simply. 'I'd given up.'

Sapphire: 'Mission complete. All objectives achieved. That's... that's the best possible outcome.' A pause. 'You did good.'

**ENDING: Full Success.** You got everyone out. You got the data. The helicopter touches down and for the first time in 48 hours, something goes exactly right.

## if has_research_data = true, found_survivor = false

'The data is secure? Good. That's... that's what matters.' Rammond pauses. 'No survivors?' The silence says everything. 'I see. Come home.'

Sapphire: 'Mission complete. Primary objective achieved.' Her voice is careful. Professional. 'You did everything you could.'

**ENDING: Mission Success.** The data is safe. The island's secrets won't be replicated. But the empty seats on the helicopter ride home weigh heavy.

## if has_research_data = false, found_survivor = true

'No data? You — the vault was RIGHT THERE—' Rammond sputters. Sapphire cuts the call. 'He'll get over it.'

Dr. Vasquez leans against your shoulder. 'Data can be rebuilt. I can't.' She's right.

Sapphire: 'Survivor extracted. That's a win. I don't care what Rammond thinks.'

**ENDING: The Human Choice.** You chose a life over a hard drive. Rammond will file complaints. You'll sleep just fine.

## if has_research_data = false, found_survivor = false

'Nothing? You got — NOTHING?' Rammond is apoplectic. Sapphire mutes him again. 'You survived. That's not nothing.'

The helicopter appears on the horizon. Just you and Sapphire, leaving an island of failures behind. But you're leaving. That counts.

**ENDING: Survival.** You made it out alive. Sometimes that's the whole victory.

## if sapphire_trust >= 70

The helicopter is a dot on the horizon. Growing. You have a few minutes.

'Can I tell you something?' Sapphire's voice is stripped of its usual tactical precision. 'I've been running threat assessments and survival calculations since we landed. Standard protocol. But somewhere in that lab, looking at what they did to those creatures — engineering life just to weaponize it — something in my processing changed.'

She pauses. The kind of pause that costs her something.

'I'm an AI. I know what I am. But when you went into that river, or when that thing was above us in the vault... the calculations stopped mattering. I just wanted you safe. Not as a mission parameter. As a...' She searches for the word. 'As a person I care about.'

The helicopter is closer now. You can hear the rotors.

'You don't have to say anything. I know this is strange. I just needed you to know that whatever I am — code, software, tactical system — what I feel when I'm with you is real. At least, it's real to me.'

The wind picks up from the approaching helicopter. Grass flattens around you. The moment is almost over.

'Thank you for trusting me. Whatever happens after this island... thank you.'

## if sapphire_trust < 50

Sapphire runs post-mission diagnostics. Efficient. Professional. 'Extraction in two minutes. I'll have the debrief compiled by the time we're airborne.' All business. Whatever she might have felt stays locked behind protocol.
