# catalog.py - the big downloads. None of this ships with Sapphire: the user clicks each one, sees its size and
# licence, and it lands under <root>/datasets/<id>/ with a receipt.json. Sizes were read from the hosts on
# 2026-10-04; the download job records what it really got.
import json
import os
import time
from pathlib import Path

from .paths import datasets_dir, write_json

HF = 'https://huggingface.co/datasets/'

DATASETS = [
    {
        'id': 'oww_models', 'label': 'openWakeWord feature models', 'for': 'oww',
        'what': 'The two small networks that turn audio into speech embeddings. Needed to train the desktop and Pi model.',
        'bytes': 1326578 + 1087958, 'licence': 'Apache-2.0', 'licence_url': 'https://github.com/dscripka/openWakeWord',
        'files': [
            {'url': 'https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/embedding_model.onnx', 'name': 'embedding_model.onnx'},
            {'url': 'https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/melspectrogram.onnx', 'name': 'melspectrogram.onnx'},
        ],
    },
    {
        'id': 'oww_negatives', 'label': 'openWakeWord negative features (ACAV100M, 2,000 hours)', 'for': 'oww',
        'what': 'Precomputed features of 2,000 hours of speech, music and noise that is not your phrase. Required for the desktop and Pi model.',
        'bytes': 17280000128, 'licence': 'CC-BY-NC-SA 4.0 (non-commercial)', 'licence_url': HF + 'davidscripka/openwakeword_features',
        'files': [{'url': HF + 'davidscripka/openwakeword_features/resolve/main/openwakeword_features_ACAV100M_2000_hrs_16bit.npy',
                   'name': 'openwakeword_features_ACAV100M_2000_hrs_16bit.npy'}],
    },
    {
        'id': 'oww_validation', 'label': 'openWakeWord validation features (11.3 hours)', 'for': 'oww',
        'what': 'Ambient recordings used to count false accepts per hour while training the desktop and Pi model. Required.',
        'bytes': 184836608, 'licence': 'CC-BY-NC-SA 4.0 (non-commercial)', 'licence_url': HF + 'davidscripka/openwakeword_features',
        'files': [{'url': HF + 'davidscripka/openwakeword_features/resolve/main/validation_set_features.npy', 'name': 'validation_set_features.npy'}],
    },
    {
        'id': 'mww_negatives', 'label': 'microWakeWord negative spectrograms (speech, noise, dinner party)', 'for': 'mww',
        'what': 'Precomputed spectrograms of speech, noise and a noisy dinner party, with an evaluation set. Required for the ESP32 model.',
        'bytes': 444310142 + 82329019 + 2000317854 + 3183001091, 'licence': 'CC-BY-NC 4.0 (non-commercial)',
        'licence_url': HF + 'kahrendt/microwakeword',
        'files': [{'url': HF + f'kahrendt/microwakeword/resolve/main/{n}.zip', 'name': f'{n}.zip', 'extract': True}
                  for n in ('dinner_party', 'dinner_party_eval', 'no_speech', 'speech')],
    },
    {
        'id': 'rirs', 'label': 'Room impulse responses (MIT survey, 270 rooms)', 'for': 'both',
        'what': 'Recordings of how 270 real rooms echo. Used to put your samples in other rooms while training. Both models.',
        'bytes': 8374902, 'licence': 'unknown (see the MIT source page)',
        'licence_url': 'https://mcdermottlab.mit.edu/Reverb/IR_Survey.html',
        'hf_snapshot': {'repo': 'davidscripka/MIT_environmental_impulse_responses', 'allow': ['16khz/*']},
    },
    {
        'id': 'music', 'label': 'Background music (Free Music Archive, extra small)', 'for': 'both',
        'what': 'Short music tracks mixed under your samples while training. Both models. Your own ambient recordings do this job too.',
        'bytes': 182339695, 'licence': 'MIT (card); tracks carry their own Creative Commons licences',
        'licence_url': HF + 'mchl914/fma_xsmall',
        'files': [{'url': HF + 'mchl914/fma_xsmall/resolve/main/fma_xs.zip', 'name': 'fma_xs.zip', 'extract': True}],
    },
]


def by_id(did):
    return next((d for d in DATASETS if d['id'] == did), None)


def dataset_dir(root, did):
    return datasets_dir(root) / did


def receipt(root, did):
    p = dataset_dir(root, did) / 'receipt.json'
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def on_disk_bytes(root, did):
    d = dataset_dir(root, did)
    if not d.is_dir():
        return 0
    total = 0
    for base, _, files in os.walk(d):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def status(root):
    """Every dataset with its state: 'ready' (receipt), 'partial' (bytes but no receipt), 'missing'."""
    out = []
    for d in DATASETS:
        r = receipt(root, d['id']) if root else None
        have = on_disk_bytes(root, d['id']) if root else 0
        state = 'ready' if r else ('partial' if have else 'missing')
        out.append({**{k: v for k, v in d.items() if k not in ('files', 'hf_snapshot')},
                    'state': state, 'on_disk': have, 'receipt': r})
    return out


def write_receipt(root, did, files):
    d = dataset_dir(root, did)
    d.mkdir(parents=True, exist_ok=True)
    data = {'id': did, 'when': time.time(), 'files': files, 'bytes': on_disk_bytes(root, did)}
    write_json(d / 'receipt.json', data, indent=2)
    return data
