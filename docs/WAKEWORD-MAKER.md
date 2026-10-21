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
3. **Synthetic.** Standard, Generate all samples. Ten minutes on a GPU. Record meanwhile.
4. **Human.** About 240 takes of the phrase is the floor; 440 is the target. Eighty per
   microphone across three microphones, the list behind the **?** on *Says it*. Then near misses and other
   words. Details in the next section.
5. **Background.** *Room day*: every microphone ticked, six hours, go live. Thirty minutes of room is the
   floor, ninety is good; a day on every mic is what the first clean models had. One take of the room when a
   whole day is more than you need. Rooms and music, the two small sets both families mix in, download here.
6. **Check.** Rate. Listen to the lowest few, keep or drop by hand.
7. **Train.** *Search*, slider on Standard, both families ticked, press Search: nine runs, about an hour. Go do
   something else. Back, read the Grids card: *Best so far* per family, ☆ to use it. Want more? Slide to Thorough
   (three variation stops, two source blocks, two nets per family, three seeds) or Deep (Thorough at two sizes,
   then Optuna around the three best desktop cells, scored by the judge). The line under the slider says how
   many runs and about how long, from this wake word's own runs. *Choose the space* shows what a stop ticks;
   change anything and it is Custom. *Does this data help?…* is the one-question search: pick a microphone or a
   tag value, it trains with and without, three seeds each, and the Grids card adds two columns for recall on
   that tag's own held-out takes. *Single* trains one recipe at a time, the knobs of each ticked family under
   *Advanced*.
8. **Train, the scoreboard.** Every finished run is judged on the held-out slice of your own recordings.
   Read *Hears you* (of your held-out takes, how many it caught), *Sound-alikes* (how many near misses
   fooled it), *Stressed* (the same takes under four rough draws each, with the held-out room behind them:
   the bench that does not saturate) and *Gap* (the lowest take's score minus the highest negative's; under
   zero they overlap). Several runs may still tie near the top; a room day decides between them.
9. **Test and Background.** Tick two or three of the best on Train. *Try it* on Test says what each heard.
   A *Room day* on Background with the TV on for an evening keeps them on watch: zero fires wins. Sort any
   fire it kept into "false alarm" or "that was me".
10. **Train.** ★ the winner of each family. **Install.** *Listen for it here* puts it on this computer, no
   restart. *Send* puts it on each satellite.

## Recording yourself, the human way

The models learn your voice from these takes, so the takes have to be the real you, not a careful reading.

- **Every microphone she will hear you through.** The satellites in the rooms where you talk to her, your
  headset, the webcam, a phone. About eighty takes each. Three or more microphones teach the phrase, not the
  microphone.
- **Per microphone:** thirty normal, ten fast, ten from across the room or the next room, ten soft or
  whispered, ten loud or excited, ten with the TV or music on.
- **Tag every take** with the four selects above the button: *How* it was said, *From* where, what *Noise* was
  on, *Who* is speaking. They stay until you change them, so set them before a batch. The held-out slice is drawn
  evenly across them, the judge reports recall by each, and Train can leave one out to ask whether it helped.
- **For the judge.** Takes recorded with that switch on are held out from the moment they land and never train.
  Three held-out takes from the next room can only say 0, 33, 67 or 100 percent; ten for the judge through each
  microphone make "does she hear me from the next room" a question with an answer.
- **Move while you do the normal ones.** Face the microphone, then a little left, a little right. Look up at
  the ceiling, then down at your hands. Say it from the doorway and from the couch. You are never square to a
  microphone when you really call her.
- **Change something every take.** Speed, distance, mood, where you face. Thirty identical takes teach
  nothing new.
- **Say it like you mean it,** as if she were across the room. No words before or after it.
- **Other people in the house:** twenty each, any way they like. One other voice is worth more than fifty more
  of yours.
- **Near misses** are words that sound like the phrase but are not it, in your own voice: "hey mark us",
  "they marcus", "hey marco". The page proposes them. Say the last word alone and the first word alone too.
  These are the hardest negatives and the best teachers. One hundred and twenty is the floor, four hundred is good.
- **Other words** are ordinary talk near her: a sentence or two per take, from several microphones, some
  whispered, some shouted. Eighty is the floor.
- **Room sound** is minutes of your rooms with nobody saying the phrase: the TV, the dishwasher, a
  conversation. Thirty minutes is the floor, ninety is good. A satellite records it in one press.
- **Test this mic** before a session: it listens to the room for two seconds and tells you if the microphone
  is noisy, quiet, or clipping.

A slice of your takes (25% unless you change it on Train) is held back and never trained on. Every model is
judged on that slice, so *Hears you* is honest, and every verdict carries recall by microphone and by each tag
(the Test breakdown, and the weakest tag under *Hears you* on the scoreboard). A thin tag gets a thin judge: if
you only whispered twice, the judge knows nothing about whispers; record some for the judge.

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
- **Synthetic.** How big a set (Quick 2,000, Standard 20,000, Thorough 60,000 clips) and which voices say the
  phrase. ▶ plays a voice saying your phrase; a voice that cannot say the word is label noise, not diversity.
  Piper's multi-speaker voices carry the variety; Kokoro adds studio voices and blends pairs into new ones.
- **Human.** Above. The same button records near misses and other words. Files recorded elsewhere upload into
  the `uploaded` collections and stay marked as yours.
- **Background.** *Room day*: every microphone at once (satellites through their doors, PC mics from the tab)
  for the hours you set, every piece into the shared room library at the data folder's root, tagged by
  microphone; every wake word trains on it and judges false alarms with it. The models ticked on Train (else
  the ★ ones) stay on watch the whole time; every fire is kept with its audio for you to sort into a negative
  (a false alarm, now a training clip) or a positive (that was me: the piece is dropped for this wake word
  only). An ESP32 with the capture door (firmware 0.7.6) pushes its room in pieces from its own task and its
  own fires are counted beside the models'; *board test mode* makes it count and answer nothing. The library
  card lists hours per microphone and the days that made them, with delete per day. *One take of the room*
  records minutes through one mic; uploads go to `ambient/uploaded`; the clip browser shows your takes and the
  library. *Rooms and music* are the two small datasets both families mix in.
- **Check.** Every clip is rated 0 to 100: Whisper transcribes it and the transcript is compared with the
  phrase, then length, level, clipping, silence and a cut-off start or end are measured. Below the bar a clip is
  dropped from training; you can overrule any verdict after listening, and your verdict stays yours. *Rate* takes
  new clips only; the select next to it rates your own recordings again, or everything again. A voice that
  mispronounces the phrase shows up low across the board: untick it on Synthetic and generate again.
- **Train.** *Variations* first: how every clip is varied on its way into training (rooms, background sound,
  distance, pitch, speed, tone), five stops from Gentle to Rough; Gentle and Natural won most often in the first
  spread. Then *Single* or *Search*. Single: which family or both, the preset (Quick, Standard, Thorough), how
  many repeats with different seeds, which synthetic voices (all, Piper only, Kokoro only, none), the knobs of
  each ticked family under Advanced. Search: a slider over four stops (Look, Standard, Thorough, Deep), each a
  grid of runs with seeds over the ticked families; Deep ends with the refine, Optuna around the grid's best
  judged desktop cells, each trial scored by the judge, the winner trained in full. The Grids card shows the
  best cell per family with ☆, the refine's state and run, and the pivot by each axis. Jobs queue one at a
  time per wake word; the strip at the top shows progress. The scoreboard lists every run with the judge's
  numbers, the threshold it picked (type another to override), ★ for the one Install uses, and a Re-judge
  button when the held-out slice changed since a verdict was written.
- **Test.** *Try it*: press, say it, see what each ticked model heard. The breakdown shows which held-out
  clip each model missed or fired on, by microphone and by tag.
- **Install.** Everything goes through the wake word library (Settings > Wakeword, `user/wakeword/models/`),
  so the ★ models land there; afterwards this computer picks from the wake word dropdown and a board from its
  own Wake word tab in Devices. *This computer*: copies the ★ desktop model into the library
  and switches the wake word setting to it, live, with a *Put back* button. *Satellites*: copies the right
  family into the library and sends it through the body's model door; the board restarts on it, about twenty
  seconds; a body that is offline is set to it and gets it when it is next seen; a body without the door says
  so and you copy the files by hand. *Files*: the model, its manifest and a copy of the judge's verdict, for
  anything else.

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
- Installed models: `user/wakeword/models/<phrase>.onnx` and `<phrase>.tflite` with one `<phrase>.json` sidecar
  (a block per family: threshold, the microWakeWord window and step, who made it). That folder is the wake
  word library (Settings > Wakeword); a model there wins over a bundled one of the same name (since 2.13.4).

## From a terminal
The same engine runs headless, in the plugin's environment:
```
conda activate sapphire-plugin-wakeword-maker
cd core/plugins/wakeword-maker
python -m wakeword_maker.cli --root /path/to/wakeword datasets
python -m wakeword_maker.cli --root /path/to/wakeword new "hey sapphire"
python -m wakeword_maker.cli --root /path/to/wakeword run synth --project hey_sapphire
python -m wakeword_maker.cli --root /path/to/wakeword run qa --project hey_sapphire --arg bar=60
python -m wakeword_maker.cli --root /path/to/wakeword run train --project hey_sapphire --arg preset=thorough
```
