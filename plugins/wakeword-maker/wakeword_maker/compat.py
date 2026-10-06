# compat.py - the shims that make today's packages run the two upstream trainers. Each one is a known rot
# spot found 2026-10-04 (tmp/wakeword-maker-plan.md); apply() runs once at the top of every heavy job.
#   - scipy >= 1.15 dropped scipy.special.sph_harm; `acoustics` (imported by openwakeword.data for coloured
#     noise) still imports it at module level.
#   - torchaudio >= 2.9 loads audio through torchcodec, which wants an FFmpeg this env does not carry;
#     openwakeword.data and speechbrain call torchaudio.load/info. soundfile does the same job.
#   - TensorFlow's XLA picks up whatever ptxas is on PATH; a system CUDA 12.2 ptxas cannot build for a
#     Blackwell card. Point it at the pip-shipped one.
import os
import sys
from pathlib import Path

_done = False


def apply():
    global _done
    if _done:
        return
    _done = True
    try:
        import scipy.special as sp
        if not hasattr(sp, 'sph_harm'):
            from scipy.special import sph_harm_y
            sp.sph_harm = lambda m, n, theta, phi: sph_harm_y(n, m, phi, theta)
    except Exception:
        pass
    try:
        import torch
        import torchaudio
        import soundfile as sf
        import numpy as np

        def _load(path, frame_offset=0, num_frames=-1, normalize=True, channels_first=True, format=None, **_):
            data, sr = sf.read(str(path), dtype='float32', always_2d=True, start=frame_offset,
                               frames=num_frames if num_frames and num_frames > 0 else -1)
            t = torch.from_numpy(np.ascontiguousarray(data.T if channels_first else data))
            return t, sr

        class _Info:
            def __init__(self, sr, n, ch):
                self.sample_rate, self.num_frames, self.num_channels = sr, n, ch

        def _info(path, format=None, **_):
            i = sf.info(str(path))
            return _Info(i.samplerate, i.frames, i.channels)

        torchaudio.load = _load
        torchaudio.info = _info
    except Exception:
        pass
    xla_dir = Path(sys.executable).parent.parent / 'lib' / f"python{sys.version_info.major}.{sys.version_info.minor}" / 'site-packages' / 'nvidia' / 'cuda_nvcc'
    if (xla_dir / 'bin').is_dir() and 'xla_gpu_cuda_data_dir' not in os.environ.get('XLA_FLAGS', ''):
        os.environ['XLA_FLAGS'] = (os.environ.get('XLA_FLAGS', '') + f" --xla_gpu_cuda_data_dir={xla_dir}").strip()
    os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '1')
