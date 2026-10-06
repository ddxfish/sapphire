# Wakeword Maker

Make Sapphire answer to your own phrase, on every device that listens for her. The Wakeword Maker is a
plugin with its own page (Apps > Wakeword Maker). It builds one dataset of your phrase, in thousands of
synthesized voices and in your own, rates it, trains two models from it, judges them on recordings it never
trained on, and installs the winner on this computer and on your satellites.

| Model | Runs on | Format |
|---|---|---|
| openWakeWord | the desktop app, the Pi satellite | `.onnx` |
| microWakeWord | the ESP32 satellite | `.tflite` + a manifest |

Both come from the same recordings and the same synthesized clips. Only the trainer differs.

## The short road

One afternoon, start to finish. Each step is a tab on the page, left to right.

1. **Settings.** Build the environment (one click, several GB, once). Download the three openWakeWord sets;
   add the microWakeWord set if you have an ESP32. The data folder is already set (see below).
2. **Wakewords.** New wake word, type the phrase. Two or three syllables with a hard consonant hear best:
   "hey marcus" beats "hi sam".
3. **Voices.** Standard, Generate all samples. Ten minutes on a GPU. Record meanwhile.
4. **Record.** About 120 takes of the phrase is the floor; 220 made the first clean models. Forty per
   microphone across three microphones, the list behind the **?** on *Says it*. Then near misses and other
   words, and fifteen minutes of your room with nobody saying the phrase. Details in the next section.
5. **Check.** Rate. Listen to the lowest few, keep or drop by hand.
6. **Train.** Leave the preset on Standard, tick both model families, set Repeats to 3 and press Train.
   Then open *Advanced*, choose model `rnn`, preset Thorough, Repeats 3, Train again. Go do something else.
7. **Train, the scoreboard.** Every finished run is judged on the held-out slice of your own recordings.
   Read *Hears you* (of your held-out takes, how many it caught) and *Sound-alikes* (how many near misses
   fooled it). Several runs will tie near the top; the room watch decides between them.
8. **Test.** Tick two or three of the best on Train, start a *Room watch* with the TV on for an evening.
   Zero fires wins. Sort any fire it kept into "false alarm" or "that was me".
9. **Train.** ★ the winner of each family. **Install.** *Listen for it here* puts it on this computer, no
   restart. *Send* puts it on each satellite.

## Recording yourself, the human way

The models learn your voice from these takes, so the takes have to be the real you, not a careful reading.

- **Every microphone she will hear you through.** The satellites in the rooms where you talk to her, your
  headset, the webcam, a phone. About forty takes each. Three or more microphones teach the phrase, not the
  microphone.
- **Per microphone:** fifteen normal, five fast, five from far away, five soft or whispered, five loud or
  excited, five with the TV or music on.
- **Move while you do the normal ones.** Face the microphone, then a little left, a little right. Look up at
  the ceiling, then down at your hands. Say it from the doorway and from the couch. You are never square to a
  microphone when you really call her.
- **Change something every take.** Speed, distance, mood, where you face. Thirty identical takes teach
  nothing new.
- **Say it like you mean it,** as if she were across the room. No words before or after it.
- **Other people in the house:** ten each, any way they like. One other voice is worth more than fifty more
  of yours.
- **Near misses** are words that sound like the phrase but are not it, in your own voice: "hey mark us",
  "they marcus", "hey marco". The page proposes them. Say the last word alone and the first word alone too.
  These are the hardest negatives and the best teachers. Sixty is the floor, two hundred is good.
- **Other words** are ordinary talk near her: a sentence or two per take, from several microphones, some
  whispered, some shouted. Forty is the floor.
- **Room sound** is minutes of your rooms with nobody saying the phrase: the TV, the dishwasher, a
  conversation. Fifteen minutes is the floor, forty-five is good. A satellite records it in one press.
- **Test this mic** before a session: it listens to the room for two seconds and tells you if the microphone
  is noisy, quiet, or clipping.

A slice of your takes (15% unless you change it on Train) is held back and never trained on. Every model is
judged on that slice, so *Hears you* is honest. A thin style gets a thin judge: if you only whispered twice,
the judge knows nothing about whispers.

## The data folder, the environment, the datasets

- **Data folder.** Everything lives here: your recordings, the synthetic clips, the downloaded datasets, the
  feature caches, the trained models. The default is `wakeword/` inside the Sapphire folder. It is kept out
  of git and out of the nightly backup on purpose, because it is big (30 GB for both families). If your
  recordings matter to you, copy `wakeword/projects/<phrase>/positive/recorded` and `negative/recorded`
  somewhere safe now and then. Change the folder on Settings to use a bigger drive.
- **Environment.** The heavy work (synthesis, rating, training) runs in the plugin's own conda environment:
  PyTorch, TensorFlow, Piper, Kokoro, Whisper. Several gigabytes, built once from Settings. An NVIDIA GPU
  makes training fast; everything also runs on the CPU, slowly for the ESP32 model. On a Mac the CPU build
  installs; on Windows the CUDA build. A "pins changed, rebuild when convenient" badge means the plugin's
  requirements moved since the build; the environment still works.
- **Datasets.** Nothing ships with Sapphire. Each set downloads when you click it, with its size and licence
  shown, and Delete removes it again. Two sets carry non-commercial licences: fine for a wake word in your
  own house, not for a product you sell.

## The stages in detail

- **Wakewords.** A card per wake word with how far along it is and the next step. Each card's **Words** button
  opens the lists: alternate spellings that should match (how the voices read it), similar words that should
  not, and the sound-alikes found for you once a set is made.
- **Voices.** How big a set (Quick 2,000, Standard 20,000, Thorough 60,000 clips) and which voices say the
  phrase. ▶ plays a voice saying your phrase; a voice that cannot say the word is label noise, not diversity.
  Piper's multi-speaker voices carry the variety; Kokoro adds studio voices and blends pairs into new ones.
- **Record.** Above. The same button records negatives and room sound. Files recorded elsewhere upload into the
  `uploaded` collections and stay marked as yours.
- **Check.** Every clip is rated 0 to 100: Whisper transcribes it and the transcript is compared with the
  phrase, then length, level, clipping, silence and a cut-off start or end are measured. Below the bar a clip is
  dropped from training; you can overrule any verdict after listening, and your verdict stays yours. *Rate* takes
  new clips only; the select next to it rates your own recordings again, or everything again. A voice that
  mispronounces the phrase shows up low across the board: untick it on Voices and generate again.
- **Train.** Which family or both, the preset (Quick, Standard, Thorough), how many repeats with different seeds,
  which synthetic voices to use (all, Piper only, Kokoro only, none), and *Variations*: how every clip is varied
  on its way into training (rooms, background sound, distance, pitch, speed, tone). Five stops from Gentle to
  Rough; Gentle and Natural won most often in the first spread. Jobs queue one at a time per wake word; the
  strip at the top shows progress. The scoreboard lists every run with the judge's numbers, the threshold it
  picked (type another to override), ★ for the one Install uses, and a Re-judge button when the held-out slice
  changed since a verdict was written.
- **Test.** *Try it*: press, say it, see what each ticked model heard. *Room watch*: a satellite or this
  browser listens for hours while you live; every fire is kept with its audio for you to sort into a negative
  (a false alarm, now a training clip) or a positive. The breakdown shows which held-out clip each model
  missed or fired on.
- **Install.** *This computer*: copies the ★ desktop model to `user/wakeword/models/` and switches the wake
  word setting to it, live, with a *Put back* button. *Satellites*: sends the right family to each one through
  its model door; a body without the door says so and you copy the files by hand. *Files*: the model, its
  manifest and a copy of the judge's verdict, for anything else.

## What happens to a job when Sapphire restarts

Jobs are processes, started by the plugin and watched through files. A plugin reload leaves them running and
picks them back up. A Sapphire restart stops them; they show as *interrupted* in the strip with a *Run again*
button, and a run that was training shows the same on Train.

## Where things land
- Clips: `wakeword/projects/<phrase>/` in collections named for what they are and where they came from
  (`positive/recorded`, `negative/uploaded`, ...). The folder is the earmark: nothing the app generates ever
  lands in a `recorded` or `uploaded` collection.
- Runs: `wakeword/projects/<phrase>/runs/<stamp>-<family>/` with `run.json`, the model, and `judge.json`.
- Voices and models the tools need: `wakeword/voices/` (Piper voices; under `hf/` the Kokoro model and packs and
  Whisper's model). Nothing is written to your home folder.
- Installed desktop models: `user/wakeword/models/<phrase>.onnx` with a `.json` sidecar that says the Wakeword
  Maker made it. Since 2.13.4 a model there wins over a bundled one of the same name.

## From a terminal
The same engine runs headless, in the plugin's environment:
```
conda activate sapphire-plugin-wakeword-maker
cd plugins/wakeword-maker
python -m wakeword_maker.cli --root /path/to/wakeword datasets
python -m wakeword_maker.cli --root /path/to/wakeword new "hey sapphire"
python -m wakeword_maker.cli --root /path/to/wakeword run synth --project hey_sapphire
python -m wakeword_maker.cli --root /path/to/wakeword run qa --project hey_sapphire --arg bar=60
python -m wakeword_maker.cli --root /path/to/wakeword run train --project hey_sapphire --arg preset=thorough
```
