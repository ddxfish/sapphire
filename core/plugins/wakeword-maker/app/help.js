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
The floor is about 240 takes of the phrase (three mics, eighty each); 440 across ten ways of speaking is the target: twice what made the first clean models, so a quarter can be held back to judge. The way you really talk to her: fast, soft, from the next room, facing away.
Your own voice counts for more than a thousand computer clips.
Use several microphones: the satellites that will listen for it, a webcam, a headset. The list behind the Says it ? button has the whole shopping list.`],
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
A voice with low scores is saying the word wrong: untick it under Voices.`],
    folder: ['The data folder', `Everything lives here: your recordings, the synthetic clips, the downloads, the trained models. Budget 30 GB for both model families.
The default is wakeword/ inside the Sapphire folder. It is kept out of git and out of the nightly backup on purpose (it is big): if your recordings matter to you, copy projects/<name>/positive/recorded and negative/recorded somewhere safe now and then.
Change it to put all of it on a bigger drive. Nothing goes in your home folder.`],
    env: ['The environment', `The training software, installed once in its own box so it can't break anything else. About 7 GB.
A graphics card makes training fast. Without one it still works, slowly.`],
    datasets: ['The datasets', `Big files other people made: hours of speech and noise she must learn to ignore, and room echoes.
Nothing downloads until you click. Delete removes it again.
Needed for the desktop and Pi model: all three openWakeWord sets. For the ESP32: the microWakeWord set.`],
    variations: ['Variations', `When training, every clip is played through rooms, noise and distance so she learns the phrase anywhere.
Five stops: Gentle (a quiet home), Mild, Natural (a living room with the TV on), Firm, Rough (a loud house). The spread of runs so far put every good model between Gentle and Rough; Gentle and Natural won most often.
Nothing is written to disk; change it and train again anytime. A saved "harsh" from before now means Rough.`],
    dryrun: ['The dry run', `Tries the recipe on 300 of your clips and asks the speech recognizer if it still hears the phrase.
A high number means the clips stay understandable. A low number means they're being buried.
Listen to the five harshest ones: if you can still hear the word, it's fine.`],
    holdout: ['Held out for testing', `A slice of your recordings and room sound (25% unless you set another share on Reshuffle) is set aside and never trained on.
After training, every model is judged on these, so Hears you and Sound-alikes are honest.
Reshuffle draws a different slice; the verdicts of older runs are then marked stale until you Re-judge.`],
    v_room: ['Rooms', `Plays the clip through a real room's echo.`],
    v_background: ['Background sound', `Mixes in your room recordings, music or static. From/to: how loud the voice is over it, in dB. 15 is quiet, 0 is as loud as the TV.`],
    v_far: ['Next room', `Muffled, quiet, echoing: said from another room.`],
    v_pitch: ['Pitch', `Voice a little higher or lower.`],
    v_speed: ['Speed', `Said a little faster or slower.`],
    v_eq: ['Tone', `Bassier, tinnier, muffled: every mic sounds different.`],
    v_notch: ['Notch', `A slice of sound missing, like a cheap speaker or a phone line.`],
    v_distortion: ['Distortion', `A little fuzz, like a speaker turned up too far. "Up to" is how much.`],
    v_level: ['Level', `Random loudness, from a whisper to a call across the room.`],
    train: ['Training', `Builds the model from your clips.
Quick: a first look, minutes. Standard: a real model. Thorough: bigger and longer, for when Standard is stuck.
The curves below update as it goes. When it's done, the model is saved and judged on your held-out clips: how many of you it heard, how many sound-alikes fooled it. That table is the scoreboard.
Thorough is a bigger net for dnn only; the rnn is one fixed LSTM and Thorough just trains it longer.
Train, then change Variations and train again to compare.`],
    validate: ['Validate on the held-out slice', `Withholds the held-out slice of your recordings to measure if it worked.
On: the slice stays out of training and you get a real Hears you % at the end.
Off: trains on all your data, but then there is nothing honest to judge it on.`],
    rounds: ['Variations per clip', `How many different versions of each clip go into training. 2 is normal. More = more variety, longer build.`],
    sweep: ['The sweep', `Tries a dozen different settings for you, each for a short run, and keeps the best.
Then trains the best one properly. About an hour on a gaming PC.`],
    test: ['Testing', `The models ticked in the Train table. Try it and Room watch run all of them side by side.
Each one's threshold and ★ live in that table too; this page is for listening.`],
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
    grid: ['The grid', `Many runs from one click. Tick what you want compared: models, variation stops, source blocks (without one microphone, synthetic only, Piper only...), how hard, named knobs; set seeds. Every combination is queued as an ordinary run, seed 1 of everything first, one at a time.
The pivot folds the finished cells by each axis: the mean of Hears you, Stressed, Sound-alikes and Gap over everything else. One row per value, so "does leaving the ESP32 takes out hurt it on the ESP32" is a glance, not a scroll. Seeds matter: the same cell three times tells noise from signal. Forgetting a grid cancels what is still queued; finished runs stay.`],
    roomday: ['Room day', `Every microphone at once, for the hours you set, everything kept. The pieces go to the shared room library at the data folder's root, tagged by microphone, and every wake word trains on them and judges false alarms with them: room sound belongs to no phrase.
If a phrase is said in the room, the models on watch flag it; "That was me" drops that piece for that wake word only. More positions beat more mics in one spot: a second ESP32 by the TV, the other Pi in another room.
PC microphones record from this tab, one stream each; keep the tab open. Satellites run on their own and survive a restart.`],
    repeats: ['Repeats', `Queue the same run two to five times, differing only by seed.
Two runs of one recipe can land a few clips apart by luck alone; three repeats and the middle one is what the recipe really does.`],
    breakdown: ['By microphone and way of speaking', `The held-out clips, scored by this model, grouped by which mic and how you said it.
A red bar says where the model is weak: record more of that, or lower the threshold a little.`],
    try: ['Try it', `Say the phrase into a mic, and every ticked model tells you what score it gave and whether it would have answered.
Try near misses and other words too. Pick a satellite to test through its own microphone.`],
    watch: ['Room watch', `Your room, for as long as you like, through a mic or a satellite. Every time a ticked model would have answered, that moment is kept with its sound.
Sort the fires: False alarm keeps it as a negative (the next training learns from it). That was me keeps it as a phrase sample.
Hours of quiet with no fires is the best news a wake word can get.
Keep it all as room sound: every piece it hears becomes a room take. A day of your room through the device's own microphone is the honest judge of false alarms, and the next training draws its background from it.
An ESP32 that pushes its room keeps hearing its wake word, and its own fires are counted beside the models'. Board test mode: it counts and answers nothing, so a candidate can be judged on a day of the room without her talking to the TV.
Several microphones at once: one watch per mic, each its own record. A whole day on every satellite and every PC mic gives each model an honest false-alarm number on its own ears and a background pool that matches it. A PC mic is one browser tab each: open the page twice for two.`],
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
