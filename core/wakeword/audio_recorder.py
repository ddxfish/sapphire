import numpy as np
import pyaudio
import array
import logging
import config

logger = logging.getLogger(__name__)

class AudioRecorder:
    def __init__(self):
        # Direct config access instead of parameters
        self.sample_rate = config.RECORDING_SAMPLE_RATE
        self.chunk_size = config.CHUNK_SIZE
        
        # Pre-allocate fixed buffer
        self.buffer = array.array('h', [0] * int(config.BUFFER_DURATION * self.sample_rate))
        self.buffer_index = 0
        
        # Frame skipping parameters
        self.frame_skip = config.FRAME_SKIP
        self.frame_counter = 0
        self.previous_result = np.array([], dtype=np.int16)
        
        try:
            self.audio = pyaudio.PyAudio()
            logger.info(f"AudioRecorder initialized: {self.sample_rate}Hz, chunk={self.chunk_size}")
        except Exception as e:
            logger.error(f"Failed to initialize PyAudio: {e}")
            raise
        
        self.stream = None

    def start_recording(self):
        try:
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size,
                start=True,
                input_device_index=None
            )
            logger.info("Audio stream opened successfully")
        except Exception as e:
            logger.error(f"Failed to open audio stream: {e}")
            raise

    def stop_recording(self):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            logger.info("Audio stream closed")
        self.audio.terminate()

    def get_stream(self):
        return self.stream

    def get_latest_chunk(self, duration):
        # Simplified frame skipping logic
        self.frame_counter = (self.frame_counter + 1) % self.frame_skip
        if self.frame_counter != 0:
            return self.previous_result
            
        chunk_size = int(duration * self.sample_rate)
        data = self.stream.read(chunk_size, exception_on_overflow=False)
        self.previous_result = np.frombuffer(data, dtype=np.int16)
        return self.previous_result