# Wakeword Maker, for an assistant at the keyboard

This page is for a coding assistant (Claude Code, a GPT harness, a script) helping someone make a wake word
with this plugin, or checking the plugin after a change. The user guide for people is `docs/WAKEWORD-MAKER.md`;
the layout and the data folder are in `README.md`. Everything the page does goes through plain HTTP doors, so
everything below can be done without the page.

## The road, in order

Each step is a tab on the page and a few doors. All doors are under `/api/plugin/wakeword-maker/`; a body is JSON
unless it says multipart. A door answers JSON, or `{"error": "..."}` with a 4xx. Jobs are processes: `POST jobs`
answers at once with a job id; `GET jobs/{id}` says `state` (queued, running, done, failed, cancelled,
interrupted), `pct`, `msg`, `result`; `GET jobs/{id}/log` is the process's log. One heavy job runs at a time per
wake word; the rest queue.

| Step | What a person does | Doors |
|---|---|---|
| Settings | build the env, download the sets | `GET status` (`env.state` ready or stale), `GET datasets`, `POST datasets/{id}/download` (oww_models, oww_negatives, oww_validation for the desktop and Pi model; mww_negatives for the ESP32; rirs and music for both) |
| Wakewords | new phrase | `POST projects {phrase}` → slug; `GET projects/{slug}` carries `progress` (which of the 7 steps are done); `PUT projects/{slug} {spellings, negatives, negatives_auto, settings, steps}` |
| Synthetic | the phrase in many voices | `GET voices`; `PUT projects/{slug} {settings: {synth: {preset, target, piper: [...], kokoro: [...]}}}`; `POST jobs {kind: synth, project}`; `GET voices/sample?engine=piper&voice=&text=` plays one |
| Human | takes of the phrase, near misses, other words | `POST projects/{slug}/record` multipart: parts `audio` (one or several WAVs, any rate), fields `collection` (positive/recorded, negative/recorded), `device`, `how`, `where`, `noise`, `who`, `judge` (held out from the start), `near`, `text`; `POST projects/{slug}/record-satellite {device, collection, seconds}` for a satellite's own mic; `POST projects/{slug}/record-test` (multipart `audio`, or `{device}`) reports floor, peak and a verdict; `GET projects/{slug}/guide` targets and checks; `GET projects/{slug}/near-misses` prompts |
| Background | hours of the rooms | `POST projects/{slug}/room-day {minutes, devices, test}` starts every satellite; a PC mic is a `POST projects/{slug}/watch {device: "browser:<name>", minutes, runs: "", keep: true}` fed by `POST projects/{slug}/watch/chunk` (multipart `audio`, field `device`) every 20 s and ended with `DELETE projects/{slug}/watch?device=`; `GET room` is the shared library; `DELETE room?watch=` removes a day |
| Check | rate every clip | `POST jobs {kind: qa, args: {bar}}`; `GET projects/{slug}/check`; `GET projects/{slug}/dropped`; `PUT projects/{slug}/clips/{kind}/{src}/{id} {verdict}` overrules |
| Train, Single | one run | `POST jobs {kind: train, args: {preset, recipe, cfg, rounds, holdout, label}}` (`train_mww` for the ESP32); `GET projects/{slug}/runs`; `GET projects/{slug}/runs/{id}` metrics; `PUT projects/{slug}/runs/{id} {chosen, threshold, label}` |
| Train, Search | a grid, then Optuna | `POST projects/{slug}/grid {trainers, recipes, sources, presets, knobs, seeds, rounds, cfg, refine: {trials, budget_steps, parallel, top}}`; `GET projects/{slug}/grids/{id}` cells with their verdicts and the refine's state; `GET train/options` the axes |
| Test | hear what each model hears | `POST projects/{slug}/try` multipart `audio` + field `runs` (comma list); `GET projects/{slug}/runs/{id}/judge` every held-out clip scored, by mic and tag; `POST jobs {kind: judge, args: {force}}` re-judges; `GET projects/{slug}/dryrun?n=` the Variations recipe's dry run |
| Install | the winner on the devices | `GET projects/{slug}/install` picks and listeners; `POST projects/{slug}/install/desktop {}` this computer, `DELETE` puts the old one back; `POST projects/{slug}/install/satellite {device}`; `GET projects/{slug}/runs/{id}/file/model.onnx` the bytes |

`cfg` on a train job or a grid is a budget laid over the preset: `{steps, layer_size, n_blocks, lr, model_type}` for
the desktop model, `{steps, eval_every, pointwise_filters, ...}` for the ESP32 one (the full lists are `DEFAULTS` in
`wakeword_maker/train_oww.py` and `train_mww.py`). A tiny budget makes a surrogate net that proves the plumbing in
seconds; a real model is the preset.

## Before trusting a change: walk the road

Three walks, one list of steps (`wakeword_maker/road.py`): Settings, Wakewords, Synthetic, Human (36 takes over
three pretend mics, nine near misses, six other words, a level check), Background (a PC-mic day of two pieces, one take, one
upload), Check, Train (a 300-step net, judged), Search (a one-cell grid and a two-trial refine with the same
budget), Test (Try it, the recipe dry run, a watch with the phrase in a piece, the fire sorted), Install (this
computer, then put back, then out of the library), done (all seven steps), cleanup (the scratch wake word, its
room pieces and its jobs). Every step answers pass, fail or skip with its seconds; a failure prints the door, what
was sent, what came back, and the job's log tail.

```
# 1. hermetic, seconds, part of every test run: the heavy jobs are stand-ins
conda run -n sapphire python -m pytest core/plugins/wakeword-maker/tests/ -q -p no:cacheprovider

# 2. the real jobs under the plugin env, in a scratch data folder where the big sets are stood in small (minutes)
WWM_ROAD=1 conda run -n sapphire python -m pytest core/plugins/wakeword-maker/tests/test_road.py -k for_real -s
#    WWM_ROAD_ESP32=1 adds the ESP32 family; WWM_ROAD_SLOW=4 on a CPU box

# 3. the running Sapphire, over HTTP: the sampler, the microphones, the satellites, the wake word library
cd core/plugins/wakeword-maker
conda run -n sapphire python -m wakeword_maker.cli road [--esp32] [--keep] [--only test,install] [--json]
```

The live walk needs an API token (Settings > System > API keys, or `bash user/sapphire-health.sh --init`; it reads
`user/sapphire-health.token` or `$SAPPHIRE_API_TOKEN`). It makes a wake word called "hey road test", walks it and
deletes it; `--keep` leaves it to look at, `--only` walks named steps on the one that was kept. Under the
`sapphire` env it also grabs three seconds of the PC microphone for the level check; every online satellite is
asked for two seconds and for a one-minute room day. Install puts the surrogate on this computer for a moment and
puts the previous wake word back. Nothing is sent to a satellite. The exit code is the number of failed steps.

## Reading a failure

- `the plugin's environment is ...`: build it on Settings (`POST /api/plugins/wakeword-maker/build-env`), several GB, once.
- `download on Settings first: ...`: the named sets; the desktop model needs three, the ESP32 one more.
- A job `failed` with a log tail: the log is the trainer's own words. `not downloaded` means a set; `CUDA` means the
  GPU; a `ModuleNotFoundError` means the env is stale (rebuild it).
- `answered 429`: core's limit of 30 writes a minute per token; the road paces itself, a script of your own should too.
- `a wake word '...' is already here and is not the road's`: a real wake word has that name; pass `--phrase`.
- A Try it score is never asserted: a 300-step surrogate owes nobody a right answer. The road proves doors, not models.

## Rules for an assistant here

- Never keep made-up audio in the shared room library (`wakeword/room/`): every wake word trains on it. The road
  deletes its own pieces; do the same (`DELETE room?watch=`).
- Never send a model to a satellite the user did not ask about: the board restarts on it.
- Never read or copy the user's recordings anywhere; they are theirs. Paths and counts are fine.
- A real run is minutes to hours (Search on Standard is about an hour on a GPU); poll `GET jobs/{id}` every few
  seconds, not faster, and read `msg` to the user.
- The page and a script share the queue: one heavy job per wake word at a time.
