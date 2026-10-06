# train_mww.py - the ESP32 trainer: microWakeWord's own trainer (its pinned source, as a subprocess with our config)
# fed our spectrogram set, the downloaded negative sets and your room. Its eval lines are turned into the same
# metrics.jsonl the page graphs. Afterwards the quantized streaming .tflite is judged the way the board judges:
# the frontend's slices in, a run of probabilities out, a sliding window over a cutoff. Recall on your held-out
# clips and false alarms per hour on your held-out room, per cutoff; then a manifest the firmware reads.
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from . import catalog, compat, features, features_mww, paths, store
from .paths import write_json

DEFAULTS = {
    'steps': 20000, 'lr': 0.001, 'batch_size': 128, 'negative_class_weight': 20, 'positive_class_weight': 1,
    'eval_every': 500, 'clip_duration_ms': 1500, 'time_mask': 0, 'freq_mask': 0,
    'pointwise_filters': '64,64,64,64', 'repeat_in_block': '1,1,1,1', 'mixconv_kernel_sizes': '[5],[7,11],[9,15],[23]',
    'residual_connection': '0,0,0,0', 'first_conv_filters': 32, 'first_conv_kernel_size': 5, 'stride': 3,
    'sampling_positive': 2.0, 'sampling_speech': 10.0, 'sampling_dinner': 10.0, 'sampling_no_speech': 5.0, 'sampling_adversarial': 3.0, 'sampling_room': 3.0,
    'penalty_adversarial': 2.0, 'seed': 1, 'sliding_window': 5,
}
PRESETS = {
    'quick': {'steps': 5000, 'eval_every': 250},
    'standard': {'steps': 20000, 'eval_every': 500},
    'thorough': {'steps': 40000, 'eval_every': 500, 'pointwise_filters': '96,96,96,96', 'first_conv_filters': 48},
}
CUTOFFS = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.97, 0.99]
NEG_SETS = (('speech', 'sampling_speech'), ('dinner_party', 'sampling_dinner'), ('no_speech', 'sampling_no_speech'))
LOG_RE = re.compile(r"Step (\d+) \(nonstreaming\): Validation: recall at no faph = ([\d.]+) with cutoff ([\d.]+), accuracy = ([\d.]+)%, recall = ([\d.]+)%, precision = ([\d.]+)%, ambient false positives = (\d+), estimated false positives per hour = ([\d.]+), loss = ([\d.]+), auc = ([\d.]+), average viable recall = ([\d.]+)")
TRAIN_RE = re.compile(r"Step #(\d+): rate ([\d.e-]+), accuracy ([\d.]+)%, recall ([\d.]+)%, precision ([\d.]+)%, cross entropy ([\d.]+)")


def negatives_dir(root):
    d = catalog.dataset_dir(root, 'mww_negatives')
    if not catalog.receipt(root, 'mww_negatives'):
        raise RuntimeError('the microWakeWord negative spectrograms are not downloaded (Settings → Datasets)')
    return d


def _find(d, name):
    """The set folder inside the unpacked zip, wherever the zip put it."""
    for cand in (d / name, *(p for p in d.rglob(name) if p.is_dir())):
        if cand.is_dir() and any(cand.glob('*/*_mmap')):
            return cand
    return None


def write_config(root, fdir, cfg, run_dir):
    neg = negatives_dir(root)
    feats = [{'features_dir': str(fdir / 'positive'), 'sampling_weight': cfg['sampling_positive'], 'penalty_weight': 1.0, 'truth': True, 'truncation_strategy': 'truncate_start', 'type': 'mmap'}]
    if (fdir / 'adversarial').is_dir():
        feats.append({'features_dir': str(fdir / 'adversarial'), 'sampling_weight': cfg['sampling_adversarial'], 'penalty_weight': cfg['penalty_adversarial'], 'truth': False, 'truncation_strategy': 'truncate_start', 'type': 'mmap'})
    if (fdir / 'room').is_dir():
        feats.append({'features_dir': str(fdir / 'room'), 'sampling_weight': cfg['sampling_room'], 'penalty_weight': 1.0, 'truth': False, 'truncation_strategy': 'random', 'type': 'mmap'})
    missing = []
    for name, wkey in NEG_SETS:
        p = _find(neg, name)
        if p:
            feats.append({'features_dir': str(p), 'sampling_weight': cfg[wkey], 'penalty_weight': 1.0, 'truth': False, 'truncation_strategy': 'random', 'type': 'mmap'})
        else:
            missing.append(name)
    p = _find(neg, 'dinner_party_eval')
    if p:
        feats.append({'features_dir': str(p), 'sampling_weight': 0.0, 'penalty_weight': 1.0, 'truth': False, 'truncation_strategy': 'split', 'type': 'mmap'})
    else:
        missing.append('dinner_party_eval')
    if missing:
        raise RuntimeError(f"negative sets missing from the download: {', '.join(missing)}")
    config = {
        'window_step_ms': features_mww.STEP_MS, 'train_dir': str(run_dir / 'mww'), 'features': feats,
        'training_steps': [int(cfg['steps'])], 'positive_class_weight': [cfg['positive_class_weight']], 'negative_class_weight': [cfg['negative_class_weight']],
        'learning_rates': [float(cfg['lr'])], 'batch_size': int(cfg['batch_size']),
        'time_mask_max_size': [int(cfg['time_mask'])], 'time_mask_count': [1 if cfg['time_mask'] else 0], 'freq_mask_max_size': [int(cfg['freq_mask'])], 'freq_mask_count': [1 if cfg['freq_mask'] else 0],
        'eval_step_interval': int(cfg['eval_every']), 'clip_duration_ms': int(cfg['clip_duration_ms']),
        'target_minimization': 0.9, 'minimization_metric': None, 'maximization_metric': 'average_viable_recall',
    }
    import yaml
    path = run_dir / 'training_parameters.yaml'
    path.write_text(yaml.safe_dump(config))
    return path


def model_args(cfg):
    return ['mixednet', '--pointwise_filters', str(cfg['pointwise_filters']), '--repeat_in_block', str(cfg['repeat_in_block']),
            '--mixconv_kernel_sizes', str(cfg['mixconv_kernel_sizes']), '--residual_connection', str(cfg['residual_connection']),
            '--first_conv_filters', str(cfg['first_conv_filters']), '--first_conv_kernel_size', str(cfg['first_conv_kernel_size']), '--stride', str(cfg['stride'])]


def run_trainer(root, run_dir, cfg, progress, log_path):
    """microWakeWord's model_train_eval as a child, its lines read as they come."""
    compat.apply()
    src = features_mww.source_dir(root)
    cmd = [sys.executable, '-m', 'microwakeword.model_train_eval', '--training_config', str(run_dir / 'training_parameters.yaml'),
           '--train', '1', '--restore_checkpoint', '1', '--test_tf_nonstreaming', '0', '--test_tflite_nonstreaming', '0',
           '--test_tflite_nonstreaming_quantized', '0', '--test_tflite_streaming', '0', '--test_tflite_streaming_quantized', '1',
           '--use_weights', 'best_weights'] + model_args(cfg)
    env = {**os.environ, 'PYTHONPATH': str(src) + os.pathsep + os.environ.get('PYTHONPATH', ''), 'TF_FORCE_GPU_ALLOW_GROWTH': 'true',
           'TF_CPP_MIN_LOG_LEVEL': '1', 'PYTHONUNBUFFERED': '1'}
    if str(cfg.get('device')) == 'cpu':
        env['CUDA_VISIBLE_DEVICES'] = ''
    steps = int(cfg['steps'])
    t0 = time.time()
    mfile = open(run_dir / 'metrics.jsonl', 'a', encoding='utf-8')
    last_train = {}
    with open(log_path, 'ab') as logf:
        proc = subprocess.Popen(cmd, cwd=str(run_dir), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1, text=True, errors='replace')
        for line in proc.stdout:
            logf.write(line.encode('utf-8', 'replace'))
            m = TRAIN_RE.search(line)
            if m:
                step = int(m.group(1))
                last_train = {'step': step, 'lr': float(m.group(2)), 'train_accuracy': float(m.group(3)) / 100, 'train_recall': float(m.group(4)) / 100, 'loss': float(m.group(6))}
                progress.update(30 + 55.0 * step / steps, f"step {step:,}/{steps:,} · loss {last_train['loss']:.4f} · {time.time() - t0:.0f}s", loss=last_train['loss'], step=step)
                continue
            m = LOG_RE.search(line)
            if m:
                step = int(m.group(1))
                row = {'t': round(time.time() - t0, 1), 'step': step, 'loss': float(m.group(9)), 'lr': last_train.get('lr'), 'train_recall': last_train.get('train_recall'),
                       'val_recall': float(m.group(5)) / 100, 'val_accuracy': float(m.group(4)) / 100, 'recall_at_no_faph': float(m.group(2)) / 100,
                       'cutoff_for_no_faph': float(m.group(3)), 'fp_per_hour': float(m.group(8)), 'ambient_fp': int(m.group(7)), 'auc': float(m.group(10)),
                       'average_viable_recall': float(m.group(11))}
                mfile.write(json.dumps(row) + '\n'); mfile.flush()
                progress.update(30 + 55.0 * step / steps, f"step {step:,}/{steps:,} · held-out recall {row['val_recall'] * 100:.0f}% · FA/h {row['fp_per_hour']:.2f} · {time.time() - t0:.0f}s", force=True)
            elif 'Converting' in line or 'Testing the TFLite' in line:
                progress.update(88, line.strip()[:100], force=True)
        proc.wait()
    mfile.close()
    if proc.returncode != 0:
        raise RuntimeError(f"microWakeWord's trainer exited with {proc.returncode}; see log.txt in the run folder")
    tflite = run_dir / 'mww' / 'tflite_stream_state_internal_quant' / 'stream_state_internal_quant.tflite'
    if not tflite.exists():
        raise RuntimeError('the trainer finished but left no quantized streaming model')
    return tflite


class Streamer:
    """The quantized streaming model driven exactly as the board drives it: `stride` slices per invoke, one byte out."""

    def __init__(self, tflite_path):
        try:
            from ai_edge_litert.interpreter import Interpreter
        except Exception:
            from tensorflow.lite import Interpreter
        self.i = Interpreter(model_path=str(tflite_path))
        self.i.allocate_tensors()
        self.inp = self.i.get_input_details()[0]
        self.out = self.i.get_output_details()[0]
        self.stride = int(self.inp['shape'][1])
        self.scale, self.zp = self.inp['quantization']

    def reset(self):
        try:
            self.i.reset_all_variables()
        except Exception:
            pass

    def probabilities(self, spec):
        """spec: (T, 40) float features. Returns one probability per invoke (every `stride` slices)."""
        q = np.clip(np.round(spec / self.scale + self.zp), -128, 127).astype(np.int8)
        out = []
        for s in range(0, len(q) - self.stride + 1, self.stride):
            self.i.set_tensor(self.inp['index'], q[s:s + self.stride][None, ...])
            self.i.invoke()
            out.append(float(self.i.get_tensor(self.out['index'])[0][0]) / 255.0)
        return np.array(out)


def _detections(probs, cutoff, window, settle):
    """Sliding-window average over the last `window` outputs, as the firmware; events with a 25-step cooldown."""
    if len(probs) < window:
        return 0
    avg = np.convolve(probs, np.ones(window) / window, mode='valid')
    hot = avg[settle:] > cutoff if len(avg) > settle else np.zeros(0, bool)
    fires, cool = 0, 0
    for h in hot:
        if cool:
            cool -= 1
            continue
        if h:
            fires += 1
            cool = 25
    return fires


def evaluate(tflite_path, fdir, cfg, root, progress=None):
    """Recall on your held-out clips and false alarms per hour on your held-out room, per cutoff; also on the
    dinner-party evaluation set (the standard ambient judge) when it is here."""
    from mmap_ninja.ragged import RaggedMmap
    st = Streamer(tflite_path)
    window = int(cfg.get('sliding_window', 5))
    settle = 100 // st.stride                 # the board ignores the first 100 slices after a reset
    silence = np.zeros((120, 40), np.float32)   # a second of nothing before each clip so the model settles
    pos = RaggedMmap(str(fdir / 'positive' / 'testing' / 'wakeword_mmap')) if (fdir / 'positive' / 'testing').is_dir() else []
    probs_pos = []
    for spec in pos:
        st.reset()
        probs_pos.append(st.probabilities(np.concatenate([silence, np.asarray(spec, np.float32)])))
    room = RaggedMmap(str(fdir / 'room' / 'testing_ambient' / 'room_mmap')) if (fdir / 'room' / 'testing_ambient').is_dir() else []
    probs_room, room_s = [], 0.0
    for spec in room:
        st.reset()
        probs_room.append(st.probabilities(np.asarray(spec, np.float32)))
        room_s += len(spec) * features_mww.STEP_MS / 1000
    std = _find(negatives_dir(root), 'dinner_party_eval')
    probs_std, std_s = [], 0.0
    if std:
        for mm in list(std.glob('testing_ambient/*_mmap'))[:1]:
            for k, spec in enumerate(RaggedMmap(str(mm))):
                if std_s > 3 * 3600:
                    break
                st.reset()
                arr = np.asarray(spec)
                if np.issubdtype(arr.dtype, np.uint16):
                    arr = arr.astype(np.float32) * 0.0390625
                probs_std.append(st.probabilities(arr.astype(np.float32)))
                std_s += len(spec) * features_mww.STEP_MS / 1000
                if progress and k % 20 == 0:
                    progress.update(92, f"judging on the dinner-party set: {std_s / 3600:.1f} h", force=True)
    rows = []
    for c in CUTOFFS:
        rec = float(np.mean([_detections(p, c, window, settle) > 0 for p in probs_pos])) if probs_pos else None
        room_fa = (sum(_detections(p, c, window, 0) for p in probs_room) / (room_s / 3600)) if room_s > 0 else None
        std_fa = (sum(_detections(p, c, window, 0) for p in probs_std) / (std_s / 3600)) if std_s > 0 else None
        rows.append({'threshold': c, 'recall': rec, 'fp_per_hour': std_fa, 'own_fa_per_hour': room_fa, 'neg_fp_rate': None})
    return rows, {'held_out_positives': len(probs_pos), 'room_seconds': int(room_s), 'std_seconds': int(std_s), 'stride': st.stride}


def pick_cutoff(rows, target_std=0.5, target_room=1.0):
    ok = [r for r in rows if r['recall'] is not None and (r['fp_per_hour'] or 0) <= target_std and (r['own_fa_per_hour'] or 0) <= target_room]
    pool = ok or [r for r in rows if r['recall'] is not None]
    if not pool:
        return 0.97
    return max(pool, key=lambda r: (r['recall'], r['threshold']))['threshold']


def manifest(phrase, cutoff, cfg, arena=None):
    return {'type': 'micro', 'wake_word': phrase, 'author': 'Wakeword Maker', 'website': '', 'model': 'model.tflite', 'trained_languages': ['en'],
            'version': 2, 'micro': {'probability_cutoff': cutoff, 'feature_step_size': features_mww.STEP_MS, 'sliding_window_size': int(cfg.get('sliding_window', 5)),
                                    'tensor_arena_size': int(arena or 24000), 'minimum_esphome_version': '2024.7.0'}}


def _recipe_of_set(fdir):
    """The recipe the feature set on disk was built with: the truth a run reports."""
    try:
        return json.loads((Path(fdir) / 'meta.json').read_text()).get('recipe') or 'natural'
    except Exception:
        return 'natural'


def run_job(root, args, progress):
    slug = args['project']
    pdir = paths.project_dir(root, slug)
    project = store.load_project(root, slug)
    preset = PRESETS.get(str(args.get('preset') or 'standard'), PRESETS['standard'])
    cfg = {**DEFAULTS, **preset, **{k: v for k, v in (args.get('cfg') or {}).items() if k in DEFAULTS}, 'device': args.get('device', 'auto')}
    progress.step('features', 'building the spectrogram set (reused if nothing changed)')
    from .train_oww import _Quiet
    key = features_mww.build(root, {'project': slug, 'recipe': args.get('recipe'), 'synth': args.get('synth'), 'exclude_near': args.get('exclude_near'), 'rounds': args.get('rounds', 2), 'holdout': args.get('holdout', True), 'seed': cfg['seed']}, _Quiet(progress))
    fdir = pdir / 'features' / 'mww' / key
    stamp = time.strftime('%Y%m%d-%H%M%S')
    run_dir = pdir / 'runs' / f"{stamp}-mww"
    run_dir.mkdir(parents=True, exist_ok=True)
    run = {'id': run_dir.name, 'trainer': 'mww', 'preset': args.get('preset', 'standard'), 'cfg': cfg, 'features': key,
           'recipe': _recipe_of_set(fdir), 'synth': args.get('synth') or 'all', 'exclude_near': args.get('exclude_near') or [], 'holdout': args.get('holdout', True),
           'started': time.time(), 'state': 'running', 'label': args.get('label') or ''}
    write_json(run_dir / 'run.json', run, indent=1)
    try:
        write_config(root, fdir, cfg, run_dir)
        progress.step('train', f"microWakeWord mixednet for {cfg['steps']:,} steps")
        tflite = run_trainer(root, run_dir, cfg, progress, run_dir / 'log.txt')
        import shutil
        shutil.copy(tflite, run_dir / 'model.tflite')
        for scratch in ('train', 'restore', 'stream_state_internal', 'tflite_stream_state_internal_quant'):     # ~60 MB of checkpoints and logs per run
            shutil.rmtree(run_dir / 'mww' / scratch, ignore_errors=True)
        progress.step('judge', 'judging the streaming model on your held-out clips and room')
        rows, info = evaluate(run_dir / 'model.tflite', fdir, cfg, root, progress)
        cutoff = pick_cutoff(rows)
        man = manifest(project['phrase'], cutoff, cfg)
        write_json(run_dir / 'model.json', man, indent=2)
        from .train_oww import score_of
        run.update({'state': 'done', 'ended': time.time(), 'rows': rows, 'score': score_of(rows), 'cutoff': cutoff, 'judge': info,
                    'seconds': round(time.time() - run['started']), 'params': os.path.getsize(run_dir / 'model.tflite')})
    except Exception as e:
        run.update({'state': 'failed', 'ended': time.time(), 'error': f"{type(e).__name__}: {e}"})
        write_json(run_dir / 'run.json', run, indent=1)
        raise
    write_json(run_dir / 'run.json', run, indent=1)
    from .train_oww import _grade
    verdict = _grade(root, slug, run_dir.name, rows, progress)        # also rewrites cutoff + model.json from the judge
    progress.done({'run': run_dir.name, 'score': run['score'], 'cutoff': (verdict or {}).get('threshold') or cutoff, 'judge': info, 'heldout': verdict})
