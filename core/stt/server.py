from flask import Flask, request, jsonify
import soundfile as sf
import numpy as np
import os
import sys
import time
import tempfile
import logging
import config

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
model = None

def initialize_model(model_size=config.STT_MODEL_SIZE, language=config.STT_LANGUAGE):
    """Initialize and load the faster-whisper STT model into memory."""
    global model
    
    logger.info(f"Loading faster-whisper model: {model_size}")
    
    try:
        from faster_whisper import WhisperModel
        import torch
        
        # Get compute settings from config
        device = getattr(config, 'FASTER_WHISPER_DEVICE', 'cuda')
        compute_type = getattr(config, 'FASTER_WHISPER_COMPUTE_TYPE', 'int8')
        num_workers = getattr(config, 'FASTER_WHISPER_NUM_WORKERS', 2)
        cuda_device = getattr(config, 'FASTER_WHISPER_CUDA_DEVICE', 1)
        
        # Define compute types to try (prioritize configured type)
        gpu_compute_types = ["int8", "int8_float16", "float16", "int8_float32"]
        if compute_type in gpu_compute_types:
            gpu_compute_types.remove(compute_type)
            gpu_compute_types.insert(0, compute_type)
            
        # Try GPU with specific device if available
        if device == "cuda" and torch.cuda.is_available():
            available_gpus = torch.cuda.device_count()
            
            if cuda_device < available_gpus:
                # Set CUDA device
                torch.cuda.set_device(cuda_device)
                device_name = torch.cuda.get_device_name(cuda_device)
                logger.info(f"Using CUDA device {cuda_device} ({device_name})")
                
                # Try each compute type
                for compute in gpu_compute_types:
                    try:
                        logger.info(f"Loading with device=cuda:{cuda_device}, compute_type={compute}")
                        model = WhisperModel(model_size, device=device, 
                                           compute_type=compute, num_workers=num_workers)
                        logger.info(f"Successfully loaded model with compute_type={compute}")
                        return True
                    except Exception as e:
                        logger.warning(f"Failed with compute_type={compute}: {e}")
            else:
                logger.warning(f"CUDA device {cuda_device} not available ({available_gpus} GPUs)")
        
        # CPU fallback
        logger.info("Falling back to CPU model with int8")
        model = WhisperModel(model_size, device="cpu", 
                           compute_type="int8", num_workers=num_workers)
        logger.info("Successfully loaded model on CPU")
        return True
            
    except ImportError as e:
        logger.error(f"Faster Whisper not installed: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to initialize model: {e}")
        return False


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


@app.route('/transcribe', methods=['POST'])
def transcribe_audio():
    """Handle transcription requests."""
    temp_path = None
    processed_path = None
    
    try:
        # Process audio file
        audio_file = request.files['audio']
        
        # Windows fix: use tempfile.mkstemp with unique names and close fd immediately
        fd, temp_path = tempfile.mkstemp(suffix=".wav", prefix="stt_input_")
        os.close(fd)  # Close fd so other processes can access the file
        
        audio_file.save(temp_path)
        
        # Load and normalize audio
        audio_data, sample_rate = sf.read(temp_path)
        if len(audio_data.shape) > 1:  # Convert stereo to mono
            audio_data = audio_data.mean(axis=1)
        
        # Normalize if audio has content
        max_val = np.max(np.abs(audio_data))
        if max_val > 0:
            audio_data = audio_data / max_val
        
        # Save preprocessed audio to unique temp file
        fd2, processed_path = tempfile.mkstemp(suffix=".wav", prefix="stt_processed_")
        os.close(fd2)
        sf.write(processed_path, audio_data, sample_rate)
        
        # Configure transcription parameters
        transcription_params = {
            'language': config.STT_LANGUAGE,
            'beam_size': getattr(config, 'FASTER_WHISPER_BEAM_SIZE', 3),
            'vad_filter': getattr(config, 'FASTER_WHISPER_VAD_FILTER', True),
            'vad_parameters': getattr(config, 'FASTER_WHISPER_VAD_PARAMETERS', None)
        }
        
        # Run transcription
        segments, _ = model.transcribe(processed_path, **transcription_params)
        text = " ".join([segment.text for segment in segments]).strip()
        
        return jsonify({"text": text})
        
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return jsonify({"error": str(e)}), 500
    
    finally:
        # Windows-safe cleanup with retries
        if processed_path:
            safe_unlink(processed_path)
        if temp_path:
            safe_unlink(temp_path)


def run_server(host=config.STT_HOST, port=config.STT_SERVER_PORT):
    """Run the Flask server."""
    app.run(host=host, port=port)