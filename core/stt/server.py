from flask import Flask, request, jsonify
import soundfile as sf
import numpy as np
import os
import sys
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

@app.route('/transcribe', methods=['POST'])
def transcribe_audio():
    """Handle transcription requests."""
    try:
        # Process audio file
        audio_file = request.files['audio']
        temp_path = 'temp_audio.wav'
        audio_file.save(temp_path)
        
        # Load and normalize audio
        audio_data, sample_rate = sf.read(temp_path)
        if len(audio_data.shape) > 1:  # Convert stereo to mono
            audio_data = audio_data.mean(axis=1)
        audio_data = audio_data / np.max(np.abs(audio_data))
        
        # Save preprocessed audio
        processed_path = temp_path + '_processed.wav'
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
        
        # Cleanup
        for path in [processed_path, temp_path]:
            if os.path.exists(path):
                os.unlink(path)
            
        return jsonify({"text": text})
        
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        return jsonify({"error": str(e)}), 500

def run_server(host=config.STT_HOST, port=config.STT_SERVER_PORT):
    """Run the Flask server."""
    app.run(host=host, port=port)