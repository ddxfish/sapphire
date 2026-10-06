# voices.py - the voice catalog the page shows and the synthesizer draws from. Static on purpose: the page works
# offline and the lists change rarely. Piper voices download on first use into <root>/voices/ (piper's own
# downloader, from rhasspy/piper-voices); Kokoro packs come from the hub on first use.
#   (name, speakers, quality, note)
PIPER_EN = [
    ('en_US-libritts_r-medium', 904, 'medium', 'LibriTTS-R: 904 American readers, the backbone of a diverse set'),
    ('en_US-libritts-high', 904, 'high', 'LibriTTS: the same 904 readers, slower, cleaner'),
    ('en_GB-vctk-medium', 109, 'medium', 'VCTK: 109 British and other accents'),
    ('en_US-l2arctic-medium', 24, 'medium', 'L2-ARCTIC: 24 non-native English accents'),
    ('en_US-arctic-medium', 18, 'medium', 'ARCTIC: 18 clear studio readers'),
    ('en_GB-aru-medium', 12, 'medium', 'ARU: 12 British voices'),
    ('en_GB-semaine-medium', 4, 'medium', 'Semaine: 4 expressive British voices'),
    ('en_US-amy-medium', 1, 'medium', 'Amy'), ('en_US-bryce-medium', 1, 'medium', 'Bryce'), ('en_US-danny-low', 1, 'low', 'Danny'),
    ('en_US-hfc_female-medium', 1, 'medium', 'HFC female'), ('en_US-hfc_male-medium', 1, 'medium', 'HFC male'),
    ('en_US-joe-medium', 1, 'medium', 'Joe'), ('en_US-john-medium', 1, 'medium', 'John'), ('en_US-kathleen-low', 1, 'low', 'Kathleen'),
    ('en_US-kristin-medium', 1, 'medium', 'Kristin'), ('en_US-kusal-medium', 1, 'medium', 'Kusal'),
    ('en_US-lessac-high', 1, 'high', 'Lessac'), ('en_US-ljspeech-high', 1, 'high', 'LJSpeech'), ('en_US-norman-medium', 1, 'medium', 'Norman'),
    ('en_US-ryan-high', 1, 'high', 'Ryan'), ('en_US-sam-medium', 1, 'medium', 'Sam'),
    ('en_GB-alan-medium', 1, 'medium', 'Alan'), ('en_GB-alba-medium', 1, 'medium', 'Alba'), ('en_GB-cori-high', 1, 'high', 'Cori'),
    ('en_GB-jenny_dioco-medium', 1, 'medium', 'Jenny'), ('en_GB-northern_english_male-medium', 1, 'medium', 'Northern English male'),
    ('en_GB-southern_english_female-low', 1, 'low', 'Southern English female'),
]
PIPER_BAD = ('en_US-l2arctic-medium', 'en_US-norman-medium')      # heard 2026-10-04: do not say the word well
PIPER_DEFAULT = [n for n, s, q, _ in PIPER_EN if q != 'low' and n not in PIPER_BAD]   # the low singles sound like it

KOKORO_EN = ['af_alloy', 'af_aoede', 'af_bella', 'af_heart', 'af_jessica', 'af_kore', 'af_nicole', 'af_nova', 'af_river',
             'af_sarah', 'af_sky', 'am_adam', 'am_echo', 'am_eric', 'am_fenrir', 'am_liam', 'am_michael', 'am_onyx',
             'am_puck', 'am_santa', 'bf_alice', 'bf_emma', 'bf_isabella', 'bf_lily', 'bm_daniel', 'bm_fable', 'bm_george', 'bm_lewis']
KOKORO_BAD = ('bf_alice', 'bm_fable')     # heard 2026-10-04: do not say the word well. Listed, unticked.
KOKORO_DEFAULT = [v for v in KOKORO_EN if v not in KOKORO_BAD]
KOKORO_REPO = 'hexgrad/Kokoro-82M'
KOKORO_MODEL_FILES = ('kokoro-v1_0.pth', 'config.json')
# Packs made for other languages still speak English text through the English pipeline, with an accent. Diversity.
KOKORO_OTHER = ['ef_dora', 'em_alex', 'em_santa', 'ff_siwis', 'hf_alpha', 'hf_beta', 'hm_omega', 'hm_psi', 'if_sara', 'im_nicola',
                'jf_alpha', 'jf_gongitsune', 'jf_nezumi', 'jf_tebukuro', 'jm_kumo', 'pf_dora', 'pm_alex', 'pm_santa',
                'zf_xiaobei', 'zf_xiaoni', 'zf_xiaoxiao', 'zf_xiaoyi', 'zm_yunjian', 'zm_yunxi', 'zm_yunxia', 'zm_yunyang']

PRESETS = {   # target clip counts for the positive set; negatives scale from them
    'quick': 2000, 'standard': 20000, 'thorough': 60000,
}


def on_disk(root):
    """Which voices are already here: piper .onnx files, kokoro packs in the data folder's hub cache, the kokoro model."""
    from .paths import hf_cache, voices_dir
    out = {'piper': [], 'kokoro': [], 'kokoro_model': False}
    if not root:
        return out
    vd = voices_dir(root)
    if vd.is_dir():
        out['piper'] = sorted(p.stem for p in vd.glob('*.onnx') if (vd / (p.name + '.json')).exists())
    repo = hf_cache(root) / ('models--' + KOKORO_REPO.replace('/', '--')) / 'snapshots'
    if repo.is_dir():
        packs, model = set(), set()
        for snap in repo.iterdir():
            packs |= {p.stem for p in (snap / 'voices').glob('*.pt')} if (snap / 'voices').is_dir() else set()
            model |= {f for f in KOKORO_MODEL_FILES if (snap / f).exists()}
        out['kokoro'] = sorted(packs)
        out['kokoro_model'] = model == set(KOKORO_MODEL_FILES)
    return out


def catalog(root=None):
    have = on_disk(root)
    return {
        'piper': [{'name': n, 'speakers': s, 'quality': q, 'note': note, 'default': n in PIPER_DEFAULT, 'on_disk': n in have['piper']}
                  for n, s, q, note in PIPER_EN],
        'kokoro': {'english': KOKORO_EN, 'other': KOKORO_OTHER, 'default': KOKORO_DEFAULT, 'on_disk': have['kokoro'],
                   'model_on_disk': have['kokoro_model']},
        'presets': PRESETS,
    }


def default_plan():
    return {'preset': 'standard', 'target': PRESETS['standard'], 'piper': PIPER_DEFAULT, 'kokoro': KOKORO_DEFAULT,
            'kokoro_other': False, 'blends': True, 'variation': 'normal', 'share_kokoro': 0.35}


def fetch(root, args, progress):
    """The voices job: download the checked voices ahead of time, into the data folder. Piper voices through piper's
    own downloader; Kokoro's model and packs through the hub, cached under <root>/voices/hf."""
    from .paths import voices_dir
    piper_names = [n for n in args.get('piper') or [] if any(n == p[0] for p in PIPER_EN)]
    kok = [v for v in args.get('kokoro') or [] if v in KOKORO_EN or v in KOKORO_OTHER]
    have = on_disk(root)
    todo_p = [n for n in piper_names if n not in have['piper']]
    todo_k = [v for v in kok if v not in have['kokoro']]
    need_model = bool(kok) and not have['kokoro_model']
    total = len(todo_p) + len(todo_k) + (1 if need_model else 0)
    if not total:
        progress.done({'fetched': 0, 'note': 'every checked voice is already here'})
        return
    done = 0
    vd = voices_dir(root)
    vd.mkdir(parents=True, exist_ok=True)
    if todo_p:
        from piper.download_voices import download_voice
        for n in todo_p:
            progress.step('piper', f"{n} ({done + 1} of {total})")
            download_voice(n, vd)
            done += 1
            progress.update(100.0 * done / total, f"{done} of {total} fetched", force=True)
    if todo_k or need_model:
        from huggingface_hub import hf_hub_download
        if need_model:
            progress.step('kokoro', f"Kokoro model ({done + 1} of {total})")
            for f in KOKORO_MODEL_FILES:
                hf_hub_download(KOKORO_REPO, f)
            done += 1
            progress.update(100.0 * done / total, f"{done} of {total} fetched", force=True)
        for v in todo_k:
            progress.step('kokoro', f"{v} ({done + 1} of {total})")
            hf_hub_download(KOKORO_REPO, f"voices/{v}.pt")
            done += 1
            progress.update(100.0 * done / total, f"{done} of {total} fetched", force=True)
    progress.done({'fetched': done, 'on_disk': on_disk(root)})
