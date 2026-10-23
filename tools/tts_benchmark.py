"""TTS Performance Benchmark — tests threading, dtype, and precision combos.

Run with the sapphire conda env:
    conda run -n sapphire python tools/tts_benchmark.py

Tests each optimization individually, then every sensible combination.
Measures inference time only (no encoding, no I/O).
"""
import os
import sys
import time
import statistics

# Ensure we can import from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

# ---------------------------------------------------------------------------
# Test sentences — mix of short and long to simulate real streaming chunks
# ---------------------------------------------------------------------------
SENTENCES = [
    "Sure, I can help with that.",
    "The quick brown fox jumps over the lazy dog near the riverbank on a warm summer afternoon.",
    "Hello! How are you doing today? I was just thinking about the weather and wondering if it might rain later this evening.",
]

WARMUP_TEXT = "Warmup sentence for the model."
ITERATIONS = 3  # per sentence per config — keeps total runtime reasonable


def run_config(label, setup_fn, teardown_fn=None):
    """Run a single benchmark configuration."""
    import torch
    from kokoro import KPipeline

    # Apply config BEFORE model load (some settings affect load behavior)
    setup_fn()

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  torch.get_num_threads(): {torch.get_num_threads()}")
    print(f"  torch.get_num_interop_threads(): {torch.get_num_interop_threads()}")

    # Load model fresh for each config to ensure clean state
    t_load = time.perf_counter()
    pipe = KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M')
    model = pipe.model
    load_ms = (time.perf_counter() - t_load) * 1000
    print(f"  Model dtype: {next(model.parameters()).dtype}")
    print(f"  Model load: {load_ms:.0f}ms")

    # Warmup — first inference is always slower (JIT, cache fill)
    for _ in range(2):
        for _, _, audio in pipe(WARMUP_TEXT, voice='af_heart', speed=1.0):
            pass

    # Benchmark each sentence
    all_times = []
    all_rtfs = []  # real-time factor: audio_duration / inference_time

    for sent_idx, text in enumerate(SENTENCES):
        times = []
        for _ in range(ITERATIONS):
            segments = []
            t0 = time.perf_counter()
            for _, _, audio in pipe(text, voice='af_heart', speed=1.0):
                if audio is not None:
                    segments.append(audio)
            elapsed = time.perf_counter() - t0
            times.append(elapsed)

            # Calculate audio duration
            if segments:
                total_samples = sum(len(s) for s in segments)
                audio_dur = total_samples / 24000.0
            else:
                audio_dur = 0

        avg = statistics.mean(times)
        best = min(times)
        if audio_dur > 0:
            rtf = audio_dur / avg
        else:
            rtf = 0

        all_times.append(avg)
        all_rtfs.append(rtf)
        print(f"  Sent {sent_idx+1} ({len(text):3d} chars): "
              f"avg={avg*1000:.0f}ms  best={best*1000:.0f}ms  "
              f"audio={audio_dur:.1f}s  RTF={rtf:.2f}x")

    overall_avg = statistics.mean(all_times) * 1000
    overall_rtf = statistics.mean(all_rtfs)

    # Cleanup
    del pipe, model
    if teardown_fn:
        teardown_fn()
    import gc; gc.collect()

    result = {
        'label': label,
        'avg_ms': overall_avg,
        'rtf': overall_rtf,
        'per_sentence': list(zip([len(s) for s in SENTENCES], all_times, all_rtfs)),
    }
    return result


def make_config(omp_threads=None, interop_threads=None, dtype=None,
                matmul_precision=None, inference_mode=False):
    """Create setup/teardown functions for a config."""
    import torch

    original = {}

    def setup():
        original['omp'] = torch.get_num_threads()
        original['interop'] = torch.get_num_interop_threads()

        if omp_threads is not None:
            torch.set_num_threads(omp_threads)
            os.environ['OMP_NUM_THREADS'] = str(omp_threads)
            os.environ['MKL_NUM_THREADS'] = str(omp_threads)

        # Note: interop threads can only be set before any parallel work
        # in some PyTorch versions. We try but it may not take effect.
        if interop_threads is not None:
            try:
                torch.set_num_interop_threads(interop_threads)
            except RuntimeError:
                pass  # already set, can't change

        if matmul_precision is not None:
            torch.set_float32_matmul_precision(matmul_precision)

        if dtype == 'bfloat16':
            os.environ['_BENCH_DTYPE'] = 'bfloat16'
        else:
            os.environ.pop('_BENCH_DTYPE', None)

    def teardown():
        torch.set_num_threads(original['omp'])
        os.environ.pop('OMP_NUM_THREADS', None)
        os.environ.pop('MKL_NUM_THREADS', None)
        os.environ.pop('_BENCH_DTYPE', None)

    return setup, teardown


def run_with_dtype_wrapper(label, setup_fn, teardown_fn, use_bf16=False):
    """Wrapper that handles bfloat16 conversion after model load."""
    import torch
    from kokoro import KPipeline

    setup_fn()

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  torch.get_num_threads(): {torch.get_num_threads()}")

    t_load = time.perf_counter()
    pipe = KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M')
    model = pipe.model

    if use_bf16:
        # Convert model to bfloat16
        model = model.to(dtype=torch.bfloat16)
        pipe.model = model
        # Also convert voices that are already loaded
        for k, v in pipe.voices.items():
            if isinstance(v, torch.Tensor):
                pipe.voices[k] = v.to(dtype=torch.bfloat16)

    load_ms = (time.perf_counter() - t_load) * 1000
    print(f"  Model dtype: {next(model.parameters()).dtype}")
    print(f"  Model load: {load_ms:.0f}ms")

    # Warmup
    for _ in range(2):
        try:
            for _, _, audio in pipe(WARMUP_TEXT, voice='af_heart', speed=1.0):
                pass
        except Exception as e:
            print(f"  ** WARMUP FAILED: {e}")
            if teardown_fn:
                teardown_fn()
            return {'label': label, 'avg_ms': -1, 'rtf': 0,
                    'per_sentence': [], 'error': str(e)}

    all_times = []
    all_rtfs = []

    for sent_idx, text in enumerate(SENTENCES):
        times = []
        audio_dur = 0
        for _ in range(ITERATIONS):
            segments = []
            t0 = time.perf_counter()
            try:
                for _, _, audio in pipe(text, voice='af_heart', speed=1.0):
                    if audio is not None:
                        segments.append(audio.float().numpy() if audio.dtype != torch.float32
                                        else audio if isinstance(audio, np.ndarray)
                                        else audio.numpy())
            except Exception as e:
                print(f"  ** Sent {sent_idx+1} FAILED: {e}")
                times.append(float('inf'))
                continue
            elapsed = time.perf_counter() - t0
            times.append(elapsed)
            if segments:
                total_samples = sum(len(s) for s in segments)
                audio_dur = total_samples / 24000.0

        times = [t for t in times if t != float('inf')]
        if not times:
            all_times.append(float('inf'))
            all_rtfs.append(0)
            continue

        avg = statistics.mean(times)
        best = min(times)
        rtf = audio_dur / avg if audio_dur > 0 else 0
        all_times.append(avg)
        all_rtfs.append(rtf)
        print(f"  Sent {sent_idx+1} ({len(text):3d} chars): "
              f"avg={avg*1000:.0f}ms  best={best*1000:.0f}ms  "
              f"audio={audio_dur:.1f}s  RTF={rtf:.2f}x")

    valid_times = [t for t in all_times if t != float('inf')]
    overall_avg = statistics.mean(valid_times) * 1000 if valid_times else -1
    overall_rtf = statistics.mean(all_rtfs) if all_rtfs else 0

    del pipe, model
    if teardown_fn:
        teardown_fn()
    import gc; gc.collect()

    return {
        'label': label,
        'avg_ms': overall_avg,
        'rtf': overall_rtf,
        'per_sentence': list(zip([len(s) for s in SENTENCES], all_times, all_rtfs)),
    }


def main():
    import torch
    print("TTS Performance Benchmark")
    print(f"Python: {sys.executable}")
    print(f"PyTorch: {torch.__version__}")
    print(f"CPU cores: {os.cpu_count()} logical")
    print(f"Default threads: {torch.get_num_threads()} intra, "
          f"{torch.get_num_interop_threads()} inter")
    print(f"Sentences: {len(SENTENCES)}, Iterations: {ITERATIONS} each")
    print(f"MKL: {torch.backends.mkl.is_available()}, "
          f"OpenMP: {torch.backends.openmp.is_available()}")

    results = []

    # -----------------------------------------------------------------------
    # Individual tests
    # -----------------------------------------------------------------------

    # 1. Baseline (defaults: 4 threads, float32)
    s, t = make_config()
    results.append(run_config("BASELINE (defaults)", s, t))

    # 2. OMP threads = 6
    s, t = make_config(omp_threads=6)
    results.append(run_config("OMP_THREADS=6", s, t))

    # 3. OMP threads = 8
    s, t = make_config(omp_threads=8)
    results.append(run_config("OMP_THREADS=8", s, t))

    # 4. Inter-op threads = 2
    s, t = make_config(interop_threads=2)
    results.append(run_config("INTEROP_THREADS=2", s, t))

    # 5. Matmul precision = medium
    s, t = make_config(matmul_precision='medium')
    results.append(run_config("MATMUL_PRECISION=medium", s, t))

    # 6. bfloat16
    s, t = make_config()
    results.append(run_with_dtype_wrapper("BFLOAT16", s, t, use_bf16=True))

    # -----------------------------------------------------------------------
    # Combinations
    # -----------------------------------------------------------------------

    # 7. OMP=8 + interop=2
    s, t = make_config(omp_threads=8, interop_threads=2)
    results.append(run_config("OMP=8 + INTEROP=2", s, t))

    # 8. OMP=8 + matmul medium
    s, t = make_config(omp_threads=8, matmul_precision='medium')
    results.append(run_config("OMP=8 + MATMUL=medium", s, t))

    # 9. OMP=6 + interop=2
    s, t = make_config(omp_threads=6, interop_threads=2)
    results.append(run_config("OMP=6 + INTEROP=2", s, t))

    # 10. OMP=8 + bf16
    s, t = make_config(omp_threads=8)
    results.append(run_with_dtype_wrapper("OMP=8 + BF16", s, t, use_bf16=True))

    # 11. OMP=6 + bf16
    s, t = make_config(omp_threads=6)
    results.append(run_with_dtype_wrapper("OMP=6 + BF16", s, t, use_bf16=True))

    # 12. OMP=8 + interop=2 + matmul medium
    s, t = make_config(omp_threads=8, interop_threads=2, matmul_precision='medium')
    results.append(run_config("OMP=8 + INTEROP=2 + MATMUL=medium", s, t))

    # 13. OMP=8 + interop=2 + bf16
    s, t = make_config(omp_threads=8, interop_threads=2)
    results.append(run_with_dtype_wrapper("OMP=8 + INTEROP=2 + BF16", s, t, use_bf16=True))

    # 14. Kitchen sink: OMP=8 + interop=2 + matmul + bf16
    s, t = make_config(omp_threads=8, interop_threads=2, matmul_precision='medium')
    results.append(run_with_dtype_wrapper("ALL: OMP=8+INTEROP=2+MATMUL+BF16", s, t, use_bf16=True))

    # 15. OMP=6 + interop=2 + bf16 (in case 6 threads is the sweet spot)
    s, t = make_config(omp_threads=6, interop_threads=2)
    results.append(run_with_dtype_wrapper("OMP=6 + INTEROP=2 + BF16", s, t, use_bf16=True))

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print(f"\n\n{'='*70}")
    print(f"  SUMMARY — sorted by average inference time")
    print(f"{'='*70}")
    print(f"  {'Config':<42} {'Avg ms':>8} {'RTF':>6} {'vs Base':>8}")
    print(f"  {'-'*42} {'-'*8} {'-'*6} {'-'*8}")

    baseline_ms = results[0]['avg_ms'] if results[0]['avg_ms'] > 0 else 1
    valid = [r for r in results if r['avg_ms'] > 0]
    valid.sort(key=lambda r: r['avg_ms'])

    for r in valid:
        pct = ((baseline_ms - r['avg_ms']) / baseline_ms) * 100
        sign = '+' if pct >= 0 else ''
        print(f"  {r['label']:<42} {r['avg_ms']:>7.0f}  {r['rtf']:>5.2f}x {sign}{pct:>6.1f}%")

    errored = [r for r in results if r['avg_ms'] <= 0]
    if errored:
        print(f"\n  FAILED:")
        for r in errored:
            print(f"    {r['label']}: {r.get('error', 'unknown error')}")

    print(f"\n  RTF = real-time factor (audio_duration / inference_time)")
    print(f"  RTF > 1.0 = faster than real-time (good)")
    print(f"  RTF < 1.0 = slower than real-time (causes gaps)")


if __name__ == '__main__':
    main()
