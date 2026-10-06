# Piano Protagonist

We have Guitar Hero at home. A Game Room game: pick a song and loop it, the
notes fall toward a keyboard drawn on screen and you play along on real keys.
Ten common chords, a shelf of old tunes, three difficulties, and a score only
when you ask for one. Nothing is kept, and she takes no part yet.

## Keys

It plays with MIDI devices and without them. The top row says where your
keys come from:

| Lane | What it is |
|---|---|
| **Sapphire's keyboard** | A MIDI keyboard or synth linked to the machine she runs on, over USB or Bluetooth, heard through the MIDI plugin's live tap. Works in every browser; Linux on her side. |
| **This browser (MIDI)** | A MIDI keyboard plugged into the machine the browser is on, through Web MIDI (Brave, Chrome, Edge). The browser asks once. |
| **Computer keys** | Laid out like a piano with middle C under `G`: `A S D F` = F3 G3 A3 B3, `G H J K L ; '` = C4 D4 E4 F4 G4 A4 B4, the black keys on the row above, `Z` / `X` to shift the octave. The caps are drawn on the keys. |

The lane that is there is picked for you; change it any time. Keys on a MIDI
device sound where they already do; **hear my keys** sounds them through the
browser as well, and is on by default for the computer keys. Typing in the
chat never plays notes.

## Playing

Pick a song from the shelf or roll the dice. **▶ Loop** runs it: a count-in,
the song, a rest of one bar, the song again, until **⏹ Stop**. Nothing covers
the stage. Change the song, the speed, the difficulty or the lane while it
runs and the loop starts over with the change in. The keys are live whether a
loop runs or not.

- **🔊 / 🔇** — lit, the song plays through the browser as it scrolls and the
  keys light as its notes sound; crossed out, the bars are silent and the only
  sound is yours.
- **Speed** — ¼, ½, ¾ or the written tempo.
- **Difficulty** — how fast the bars fall, how close a key has to be, and what
  is scored (the table below). The dice roll songs at or under it.
- **Calibrate** — eight clicks, press any key on each; your lag (Bluetooth,
  synth, reflexes) is measured once per lane and taken out of the timing when
  a pass is scored.

## Scoring

Off until you light **✨ Score**. Then each pass you play leaves a number in
the upper right, with a burst of sparks. A caught note glows and pops, and a
clean one sparks; a miss or a wrong key draws nothing, a pass you sit out
leaves the number alone, and keys played during the rest count for nothing.
A score starts with a whole pass.

| | Window | Hold | Octave | Labels |
|---|---|---|---|---|
| easy | ±300 ms | not scored | any | on |
| medium | ±220 ms | scored | any | on |
| hard | ±140 ms | scored | the written one | chord names only |

The window is how far from its line a key still catches its note, early or
late. The number is a product, so every part counts:

```
SCORE = pitch × timing × hold × streak
```

- **pitch** — notes caught, minus half a note per wrong key: one the song is
  not asking for anywhere near. Striking a due note twice, or too late to
  catch it, costs nothing more than the note.
- **timing** — how close to the line, squared: full on the line, nothing at
  the edge of the window. On easy a note 150 ms late is still 75%.
- **hold** — medium and hard: you kept the key down for the written length
  (short notes don't count).
- **streak** — 70% plus 30% for your longest clean run.

Key velocity is never scored: the FM-1's keys don't report it, and a game
should play the same on every keyboard. Each scored pass writes one line to
the browser console saying how its number was made.

## Your songs

Settings → Songs, one per line: `name | bpm | notation`, with an optional
`easy` / `medium` / `hard` before the notation.

```
Row Your Boat | 120 | C4 C4 C4:0.75 D4:0.25 E4 E4:0.75 D4:0.25 E4:0.75 F4:0.25 G4:2
Four chords, quick | 120 | hard | C:2 G:2 Am:2 F:2
```

Notes are `C4 E4 G4:2` (middle C is C4, `:beats` is the length, `R` a rest);
a chord name with no octave — `C`, `Am`, `F#m`, `G7` — is the whole chord.
Lines that don't parse are named under the shelf. The shipped tunes are
traditional or published before 1929 and transcribed by hand; keep your own
additions yours.

## Her part

None yet. Her turns are off in this room and the board sends her nothing. The
chat rail is still the room's own, so you can talk to her there; she just
wasn't watching.

## Files

```
plugin.json                          games capability (id piano-protagonist, her turns off)
games/piano-protagonist/engine.py    the session and the "Your songs" setting, nothing else
app/piano-protagonist.js             the board (free-mount)
app/pp/notation.js                   the notation, the same one the MIDI plugin's tools read
app/pp/lanes.js                      server tap · Web MIDI · computer keys
app/pp/loop.js                       the song on repeat: passes on the lane's clock
app/pp/score.js                      the judge, when asked
app/pp/highway.js                    the falling notes, the corner number, the sparks
app/pp/audio.js                      the little piano the song plays on
app/pp/content.json                  chords and songs
```

The "Sapphire's keyboard" lane needs the MIDI plugin on her machine
(`GET /api/plugin/midi/tap`). Without it the lane says so and the other
two stand.
