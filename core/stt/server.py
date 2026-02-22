import soundfile as sf
import numpy as np
import os
import time
import tempfile
import logging
import threading
from typing import Optional

import config

logger = logging.getLogger(__name__)


def safe_unlink(path, retries=3, delay=0.2):
    """Windows-safe file deletion with retries."""
    for attempt in range(retries):
        try:
            if os.path.exists(path):
                os.unlink(path)
            return True
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                logger.warning(f"Could not delete temp file after {retries} attempts: {path}")
                return False
        except Exception as e:
            logger.warning(f"Error deleting {path}: {e}")
            return False
    return True


class WhisperSTT:
    """Direct faster-whisper STT — no HTTP, no Flask, just transcribe."""

    def __init__(self, model_size=config.STT_MODEL_SIZE, language=config.STT_LANGUAGE):
        logger.info(f"Loading faster-whisper model: {model_size}")
        self.model = None
        self._lock = threading.Lock()

        try:
            from faster_whisper import WhisperModel
            import torch

            device = getattr(config, 'FASTER_WHISPER_DEVICE', 'cuda')
            compute_type = getattr(config, 'FASTER_WHISPER_COMPUTE_TYPE', 'int8')
            num_workers = getattr(config, 'FASTER_WHISPER_NUM_WORKERS', 2)
            cuda_device = getattr(config, 'FASTER_WHISPER_CUDA_DEVICE', 0)

            # Define compute types to try (prioritize configured type)
            gpu_compute_types = ["int8", "int8_float16", "float16", "int8_float32"]
            if compute_type in gpu_compute_types:
                gpu_compute_types.remove(compute_type)
                gpu_compute_types.insert(0, compute_type)

            # Try GPU with specific device if available
            if device == "cuda" and torch.cuda.is_available():
                available_gpus = torch.cuda.device_count()

                if cuda_device < available_gpus:
                    torch.cuda.set_device(cuda_device)
                    device_name = torch.cuda.get_device_name(cuda_device)
                    logger.info(f"Using CUDA device {cuda_device} ({device_name})")

                    for compute in gpu_compute_types:
                        try:
                            logger.info(f"Loading with device=cuda:{cuda_device}, compute_type={compute}")
                            self.model = WhisperModel(model_size, device=device,
                                                      compute_type=compute, num_workers=num_workers)
                            logger.info(f"Successfully loaded model with compute_type={compute}")
                            return
                        except Exception as e:
                            logger.warning(f"Failed with compute_type={compute}: {e}")
                else:
                    logger.warning(f"CUDA device {cuda_device} not available ({available_gpus} GPUs)")

            # CPU fallback
            logger.info("Falling back to CPU model with int8")
            self.model = WhisperModel(model_size, device="cpu",
                                      compute_type="int8", num_workers=num_workers)
            logger.info("Successfully loaded model on CPU")

        except ImportError as e:
            raise RuntimeError(f"Faster Whisper not installed: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize STT model: {e}")

    def transcribe_file(self, audio_file: str) -> Optional[str]:
        """Transcribe an audio file and return the text. Thread-safe."""
        temp_path = None

        with self._lock:
            try:
                # Load and normalize audio
                audio_data, sample_rate = sf.read(audio_file)
                if len(audio_data.shape) > 1:  # Convert stereo to mono
                    audio_data = audio_data.mean(axis=1)

                max_val = np.max(np.abs(audio_data))
                if max_val > 0:
                    audio_data = audio_data / max_val

                # Save preprocessed audio to temp file
                fd, temp_path = tempfile.mkstemp(suffix=".wav", prefix="stt_processed_")
                os.close(fd)
                sf.write(temp_path, audio_data, sample_rate)

                # Configure transcription parameters
                transcription_params = {
                    'language': config.STT_LANGUAGE,
                    'beam_size': getattr(config, 'FASTER_WHISPER_BEAM_SIZE', 3),
                    'vad_filter': getattr(config, 'FASTER_WHISPER_VAD_FILTER', True),
                    'vad_parameters': getattr(config, 'FASTER_WHISPER_VAD_PARAMETERS', None)
                }

                segments, _ = self.model.transcribe(temp_path, **transcription_params)
                text = " ".join([segment.text for segment in segments]).strip()
                return text

            except Exception as e:
                logger.error(f"Transcription error: {e}")
                return None

            finally:
                if temp_path:
                    safe_unlink(temp_path)
