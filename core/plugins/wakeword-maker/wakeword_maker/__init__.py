# wakeword_maker - the engine behind the Wakeword Maker plugin (tmp/wakeword-maker-plan.md).
#
# Two sides import this package:
#   Sapphire's own process (routes/, daemon.py): paths, store, catalog, jobs. Light modules, no torch, no TensorFlow.
#   the plugin's conda env, as a job process or the CLI (`python -m wakeword_maker.cli`): everything, including
#   synth (piper, kokoro), qa (whisper), the two trainers. Heavy imports live inside functions so the light side
#   never pays for them.
