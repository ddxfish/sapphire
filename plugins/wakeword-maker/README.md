# Wakeword Maker

Make your own wake word inside Sapphire. One dataset of your phrase, synthesized in thousands of voices and
recorded in your own, rated, then trained two ways: **openWakeWord** for the desktop and the Pi satellite,
**microWakeWord** for the ESP32 satellite. Every model is judged on recordings it never trained on, and the
winner installs from the page. User guide, with the short road and how to record: `docs/WAKEWORD-MAKER.md`.

## Easy training, in one paragraph
Settings: build the environment, download the datasets. Wakewords: new phrase. Voices: Standard, generate.
Record: about 120 takes of the phrase over three microphones (the list is behind the **?** on *Says it*),
near misses, other words, fifteen minutes of room. Check: rate. Train: Standard with Repeats 3, then
`rnn` + Thorough with Repeats 3. Read the scoreboard, run a Room watch on Test for an evening, ★ the winner,
Install. The first clean models came from 223 takes over four microphones and ten ways of speaking, 219 near
misses, 175 other words and 48 minutes of room: 100% of the held-out takes heard, zero sound-alikes, zero
fires in 4.4 hours of television.

## Layout
```
plugin.json          manifest: own conda env (pip pins with platform markers), app page, routes, settings
daemon.py            on load: re-attach running jobs, resume a paused room watch; on unload: detach, pause
routes/api.py        the page's doors: projects, clips, record/upload, check, train, runs, judge, watch, install
wakeword_maker/
  paths.py           the data folder (default <sapphire>/wakeword), collections, atomic write_json/read_json
  store.py           projects and clips: create/move/delete with the empty-folder rule, decode, trim, sidecars
  jobs.py            jobs are processes: queue, claim, tail, cancel, interrupted, prune; pid + birth stamp
  cli.py             `python -m wakeword_maker.cli job <dir>` (what jobs run), and the terminal commands
  catalog.py download.py   the datasets, with licences and resumable downloads
  voices.py synth.py       Piper and Kokoro voices, the synthetic set
  qa.py              Whisper rating of every clip, verdicts (the user's verdict always wins)
  augment.py         variations: rooms, background, distance, pitch, speed, tone; five stops
  holdout.py         the held-out slice of your recordings and its key
  features.py features_mww.py   feature sets per family (cached by content key, pruned to the newest three)
  train_oww.py train_mww.py tune.py   the two trainers and the sweep
  judge.py           the streaming judge: every run scored on the held-out slice, the threshold pick
  watch.py           room watch: hours of listening, every fire kept with its audio
  sampler.py sampler_client.py   a warm helper process for previews, Try it and the judge
  negatives.py compat.py   sound-alike texts; shims for known rot in the stack
app/                 the page: index.js (shell, status strip, job events), api.js, recorder.js, help.js, tabs/*.js
tests/               hermetic tests: engine, store, routes, judge, install (no torch or tensorflow needed)
```

## Jobs are processes
Every piece of heavy work runs as `<env python> -m wakeword_maker.cli job <job dir>` in its own session. It
writes `progress.jsonl` and its own epitaph in `status.json`; Sapphire tails the file and publishes
`wakeword_maker.job` on the event bus. One heavy job at a time per wake word; the rest queue. `job.json`
records the pid and the process start time, so a recycled pid is never mistaken for a live job. A plugin
reload leaves jobs running and re-attaches; a Sapphire restart stops them and they report `interrupted` with
a Run again button. The same work runs from a terminal:
```
conda activate sapphire-plugin-wakeword-maker
cd plugins/wakeword-maker
python -m wakeword_maker.cli --root /path/to/wakeword new "hey sapphire"
python -m wakeword_maker.cli --root /path/to/wakeword run synth --project hey_sapphire
python -m wakeword_maker.cli --root /path/to/wakeword run qa --project hey_sapphire --arg bar=60
python -m wakeword_maker.cli --root /path/to/wakeword run train --project hey_sapphire --arg preset=thorough
```

## Data on disk
```
<data_dir>/                      default <sapphire>/wakeword, in .gitignore, outside the nightly backup
  datasets/<id>/                 downloads with a receipt.json (never shipped; the user clicks each one)
  voices/                        Piper voices; hf/ holds the Kokoro and Whisper models
  tools/                         the pinned microWakeWord source, fetched on the first ESP32 build
  projects/<slug>/               project.json, holdout.json, and the collections:
     positive/{synth,recorded,uploaded}  negative/{synth,recorded,uploaded,mined}  ambient/{recorded,uploaded}
     each clip = <id>.wav (16 kHz mono) + <id>.json (source, device, voice, style, text, qa, verdict)
     qa/summary.json qa/worst.json   features/{oww,mww}/<key>/   runs/<stamp>-<family>/   watch/<id>/
  jobs/<id>/                     job.json status.json progress.jsonl log.txt (newest 100 kept)
```
The folder is the earmark: nothing the app generates ever lands in a `recorded` or `uploaded` collection.
Installed desktop models go to `user/wakeword/models/<slug>.onnx` + `.json`; core loads a model there before a
bundled one of the same name.

## Third-party pieces
None are shipped in this folder. The plugin's environment installs openWakeWord, Piper, Kokoro, faster-whisper,
TensorFlow and PyTorch from pip; the microWakeWord trainer (Apache-2.0) is fetched as a pinned tarball into the
data folder on first use; the datasets download on the user's click with their licences shown, two of them
non-commercial.

## Known rot, shimmed in `compat.py`
scipy >= 1.15 vs `acoustics`; torchaudio >= 2.9 vs torchcodec/FFmpeg; XLA finding a system ptxas that cannot
target a Blackwell GPU.
