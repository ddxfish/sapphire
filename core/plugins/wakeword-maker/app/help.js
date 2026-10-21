// help.js - the words behind every "?". Short, plain, for people who are not engineers. One idea per line.
import { showHelpModal } from '/static/shared/modal.js';

export const HELP = {
    size: ['Make the samples', `Makes thousands of clips of your phrase in computer voices.
Quick: 2,000, a few minutes. Standard: 20,000, about 10 minutes on a gaming PC. Thorough: 60,000.
On a PC without a good graphics card, count on an hour or more.
Running it again fills up to the number and removes clips from voices you unticked.`],
    voices: ['Voices', `Press ▶ to hear a voice say your phrase. If it can't say it, untick it.
● means the voice is on your disk, ○ means it downloads when first used.
"Download checked" fetches them all now.`],
    variety: ['Speech variety', `How differently the voices speak: speed, breath, energy.
Normal is how people really say a wake word. Low is careful. High is sloppy to over-the-top.`],
    blends: ['Blend pairs', `Mixes two voices into a new one nobody has heard. More variety for free.
Press ▶ to hear one.`],
    other_packs: ['Other-language packs', `Voices made for other languages, speaking English with an accent.
Some are good variety, some mangle the word. Listen before you keep them.`],
    record: ['Recording yourself', `Press, say it, press again. That's one sample.
The floor is about 240 takes of the phrase (three mics, eighty each); 440 across the ways you really say it is the target: twice what made the first clean models, so a quarter can be held back to judge. The way you really talk to her: fast, soft, from the next room, facing away.
Tag every take: how you said it, where from, what was on, who. The judge reports by each tag.
Your own voice counts for more than a thousand computer clips.
Use several microphones: the satellites that will listen for it, a webcam, a headset. The list behind the Says it ? button has the whole shopping list.`],
    tags: ['The tags', `Four tags on every take, remembered until you change them: How you said it (normal, fast, soft, loud, whisper...), From where (close, across the room, the next room), Noise (quiet, the TV or music on, a busy room), Who (whose voice).
Set them before a batch, not after. The held-out slice is drawn evenly across them, the judge reports recall by each one, and "Does this data help?" on Train can leave one out to see what it was worth.
A take with the TV or music on through a satellite records a fixed 4 seconds: press, say it, done.`],
    judge_mark: ['For the judge', `Takes recorded with this on are held out from the moment they land: they judge every model and never train one.
A thin tag gets a thin judge: three held-out takes from the next room can only say 0, 33, 67 or 100 percent. Ten takes for the judge from the next room, through each mic, and the question "does it hear me from the next room" has an answer.
Switch it off for the takes that should teach her. Both kinds are yours; the judge takes just never cross over.`],
    upload: ['Uploading files', `wav, mp3, flac, ogg, or a zip of them.
They stay marked as yours.`],
    guide: ['The guide', `Targets, floor and good: 240 and 440 of your phrase, 120 and 400 near misses, 80 and 320 other words, 30 and 90 minutes of room. The good column is twice what made the first clean models: a quarter of it is held back to judge.
✗ fix before training. ⚠ should fix. ℹ would make it better.
"Test this mic" listens to the room for two seconds and tells you if the mic is noisy or quiet.`],
    satmic: ['Satellite microphone', `Mic gain: how loud the device's microphone is. Raise it until "Test this mic" reads good.
AGC: the device adjusts its own gain. Off is better for training. The device restarts when you change it.`],
    check: ['Checking the clips', `Every clip gets a score from 0 to 100.
Does it say the phrase? Is it too quiet, too loud, too short? 
Below the bar it is dropped from training. Listen to the lowest and keep or drop by hand.
A voice with low scores is saying the word wrong: untick it under Synthetic.`],
    folder: ['The data folder', `Everything lives here: your recordings, the synthetic clips, the downloads, the trained models. Budget 30 GB for both model families.
The default is wakeword/ inside the Sapphire folder. It is kept out of git and out of the nightly backup on purpose (it is big): if your recordings matter to you, copy projects/<name>/positive/recorded and negative/recorded somewhere safe now and then.
Change it to put all of it on a bigger drive. Nothing goes in your home folder.`],
    env: ['The environment', `The training software, installed once in its own box so it can't break anything else. About 7 GB.
A graphics card makes training fast. Without one it still works, slowly.`],
    datasets: ['The trainer sets', `Big files other people made: hours of speech and noise she must learn to ignore, in the form each trainer eats.
Nothing downloads until you click. Delete removes it again.
Needed for the desktop and Pi model: all three openWakeWord sets. For the ESP32: the microWakeWord set.
The two small sets both families mix in, rooms and music, are on Background.`],
    variations: ['Variations', `When training, every clip is played through rooms, noise and distance so she learns the phrase anywhere.
Five stops: Gentle (a quiet home), Mild, Natural (a living room with the TV on), Firm, Rough (a loud house). The spread of runs so far put every good model between Gentle and Rough; Gentle and Natural won most often.
Nothing is written to disk; change it and train again anytime. A saved "harsh" from before now means Rough.`],
    dryrun: ['The dry run', `Tries the recipe on 300 of your clips and asks the speech recognizer if it still hears the phrase.
A high number means the clips stay understandable. A low number means they're being buried.
Listen to the five harshest ones: if you can still hear the word, it's fine.`],
    holdout: ['Held out for testing', `A slice of your recordings and room sound (25% unless you set another share on Reshuffle) is set aside and never trained on, drawn evenly across your mics and tags.
After training, every model is judged on these, so Hears you and Sound-alikes are honest, and the verdict carries recall by mic and by every tag.
Takes recorded "for the judge" are always in the slice, on top of the draw. Reshuffle draws a different slice; the verdicts of older runs are then marked stale until you Re-judge.`],
    v_room: ['Rooms', `Plays the clip through a real room's echo.`],
    v_background: ['Background sound', `Mixes in your room recordings, music or static. From/to: how loud the voice is over it, in dB. 15 is quiet, 0 is as loud as the TV.`],
    v_far: ['Next room', `Muffled, quiet, echoing: said from another room.`],
    v_pitch: ['Pitch', `Voice a little higher or lower.`],
    v_speed: ['Speed', `Said a little faster or slower.`],
    v_eq: ['Tone', `Bassier, tinnier, muffled: every mic sounds different.`],
    v_notch: ['Notch', `A slice of sound missing, like a cheap speaker or a phone line.`],
    v_distortion: ['Distortion', `A little fuzz, like a speaker turned up too far. "Up to" is how much.`],
    v_level: ['Level', `Random loudness, from a whisper to a call across the room.`],
    train: ['Training', `Single builds one model per ticked family from your clips, with the recipe above and how hard you say. Quick: a first look, minutes. Standard: a real model. Thorough: bigger and longer, for when Standard is stuck. Advanced shows each ticked family's knobs.
Search queues many at once: a slider from Look to Deep, each stop a space of runs with seeds, and Deep ends with Optuna around the best. The Grids card reads the result.
Every finished model is judged on your held-out clips: how many of you it heard, how many sound-alikes fooled it, how it holds up under stress. That table is the scoreboard.
Thorough is a bigger net for dnn only; the rnn is one fixed LSTM and Thorough just trains it longer.`],
    search: ['Search', `Four stops. Look: one quick run per model, is the data any good. Standard: the two desktop nets and the ESP32 net, three seeds each. Thorough: three variation stops, two source blocks, two nets per family, three seeds. Deep: Thorough at two sizes, then the refine.
The line under the slider says how many runs and about how long, from this wake word's own runs so far. Choose the space shows what a stop ticks; change anything and it is Custom. Seeds matter: the same cell three times tells noise from signal.
Everything lands as ordinary runs on the scoreboard, and the Grids card folds them by each axis with the best cell per family and a ★ to use it.`],
    refine: ['The refine', `Desktop and Pi only. After the grid, Optuna starts from the best judged cells (their recipe, data and knobs) and walks the knobs around them: each trial a short run, scored by the same judge as the scoreboard (0.3 × hears you + 0.7 × stressed, a tenth off per sound-alike that fires, a third off when another word fires). The best trial then trains in full and is judged like any run.
The grid finds the candidates, the refine masters them. On its own, with nothing judged yet, it is a plain sweep over the whole space.`],
    validate: ['Validate on the held-out slice', `Withholds the held-out slice of your recordings to measure if it worked.
On: the slice stays out of training and you get a real Hears you % at the end.
Off: trains on all your data, but then there is nothing honest to judge it on.`],
    rounds: ['Variations per clip', `How many different versions of each clip go into training. 2 is normal. More = more variety, longer build.`],
    test: ['Testing', `The models ticked in the Train table. Try it runs all of them side by side; the breakdown shows where each one is weak.
Each one's threshold and ★ live in that table too; this page is for listening. The room, for hours, is Background's Room day: the same ticked models stay on watch there.`],
    synth: ['Synthetic voices', `Which made voices train this run. All, one engine, or none (only what you recorded).
Piper brings hundreds of plain readers; Kokoro brings a few dozen polished ones. Train the same run both ways and the judge says which your room prefers.`],
    install: ['Install', `Puts the ★ models where they listen, through the wake word library (Settings > Wakeword): the models land there; afterwards this computer picks from the wake word dropdown and a board from its own Wake word tab in Devices. This computer swaps live, no restart.
A satellite takes its model through a door in its body (PUT /wakeword/model); a body without the door gets the files copied by hand.
Each kind of device wants its own kind of model: openWakeWord for desktop and Pi, microWakeWord for the ESP32.`],
    install_desktop: ['This computer', `Copies the model into the library (user/wakeword/models), sets the wake word model and threshold, and swaps the live detector.
"Put back" returns to the model that was listening before. Both stay on disk; Settings can pick either.`],
    install_satellite: ['Satellites', `Copies the model into the library, then sends it to the body through its door (PUT /wakeword/model): the body stores it and restarts on it, about twenty seconds. A body that cannot be reached now is set to it and gets it when it is next seen.
Pi and ESP32 bodies older than that door answer "no door": copy the files instead.`],
    sources: ['Sources', `What this run trains on. Synthetic voices: all, one engine, or none. Your takes: all, or none (then the model learns the phrase from made voices alone and is judged on you). Own weight: how many extra times each of your takes is varied into training, 8 by default against thousands of synthetic clips.
The held-out slice is never touched by any of this: every run is judged on every microphone. Your own near misses always train.`],
    grid: ['The grids', `Every search is a grid: every combination of what the space ticks (models, variation stops, source blocks, how hard, named knobs), seeds times, queued as ordinary runs, seed 1 of everything first.
Best so far: the best judged cell per family (clean first, then stressed recall), with ☆ to make it the one Install uses. The refine line follows the Optuna job and its final run.
The pivot folds the finished cells by each axis: the mean of Hears you, Stressed, Sound-alikes and Gap over everything else. One row per value, so "does leaving the ESP32 takes out hurt it on the ESP32" is a glance, not a scroll. Forgetting a grid cancels what is still queued, the refine too; finished runs stay.`],
    roomday: ['Room day', `Every microphone at once, for the hours you set, everything kept. The pieces go to the shared room library, tagged by microphone, and every wake word trains on them and judges false alarms with them: room sound belongs to no phrase.
The models on watch (the ones ticked on Train, else the ★ ones) listen the whole time. Every time one would have answered, the moment is kept below for you to sort: False alarm keeps it as a negative for the next training; That was me keeps it as a phrase sample and drops that piece for this wake word only. Hours of quiet with no fires is the best news a wake word can get.
An ESP32 that pushes its room keeps answering its wake word and its own fires are counted beside the models'; board test mode makes it count and answer nothing. A Pi won't answer while it records.
More positions beat more mics in one spot: a second ESP32 by the TV, the other Pi in another room. PC microphones record from this tab, one stream each; keep the tab open. Satellites run on their own and survive a restart.`],
    library: ['The room library', `Every piece of room sound, shared by every wake word, tagged by microphone and by the day that kept it. The held-out slice is drawn from it per microphone, so each model is judged for false alarms on its own ears, and training draws its backgrounds from the rest.
Delete a day to drop every piece it kept (an empty house with the TV off teaches little). A piece the phrase was said in is dropped for that wake word only, from That was me.`],
    room_take: ['One take of the room', `Minutes of your room as it really is, when a whole day is more than you need: TV, dishes, music, typing, quiet. Press, let it run, press again. A satellite records for the length you pick, an ESP32 one minute at a time.
Never say the phrase during a take, leave the mic where it lives, and don't make the room louder than it really is. These takes belong to this wake word; a Room day's pieces belong to every wake word.`],
    bg_datasets: ['Rooms and music', `Two small sets both model families mix in while training: how 270 real rooms echo, and short music tracks played under your samples. One click each; your own room sound does the music's job too.
The trainer sets, the hours of negatives each family needs, are on Settings.`],
    repeats: ['Repeats', `Queue the same run two to five times, differing only by seed.
Two runs of one recipe can land a few clips apart by luck alone; three repeats and the middle one is what the recipe really does.`],
    breakdown: ['By microphone and tag', `The held-out clips, scored by this model, grouped by which mic, how you said it, where from, what was on and who.
A red bar says where the model is weak: record more of that (for the judge too, so the bar means something), or lower the threshold a little.
A table with one row is left out; a tag with two or three takes behind it is a hint, not a verdict.`],
    ablate: ['Does this data help?', `Pick a mic or a tag value. Two source blocks are queued, everything and everything without it, the ticked models, several seeds each, as one grid.
The Grids card then shows two extra columns: recall and stress recall on the held-out takes that carry that tag. If "all" beats "without" there by more than the seeds spread, the data earns its keep; if not, it was weight, not signal.
The judge needs takes of that tag to say anything: record ten for the judge first.`],
    try: ['Try it', `Say the phrase into a mic, and every ticked model tells you what score it gave and whether it would have answered.
Try near misses and other words too. Pick a satellite to test through its own microphone.`],
};

export function qbtn(topic) {
    return `<button type="button" class="wwm-q" data-help="${topic}" title="What is this?">?</button>`;
}

export function bindHelp(el) {
    el.querySelectorAll('.wwm-q[data-help]').forEach(b => b.addEventListener('click', (e) => {
        e.preventDefault(); e.stopPropagation();
        const h = HELP[b.dataset.help];
        if (h) showHelpModal(h[0], h[1]);
    }));
}
