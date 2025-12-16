import numpy as np
import sounddevice as sd
import array
import logging
import config

logger = logging.getLogger(__name__)


class AudioRecorder:
    """Audio recorder for wake word detection using sounddevice."""
    
    def __init__(self):
        self.sample_rate = config.RECORDING_SAMPLE_RATE
        self.chunk_size = config.CHUNK_SIZE
        
        # Pre-allocate fixed buffer
        self.buffer = array.array('h', [0] * int(config.BUFFER_DURATION * self.sample_rate))
        self.buffer_index = 0
        
        # Frame skipping parameters
        self.frame_skip = config.FRAME_SKIP
        self.frame_counter = 0
        self.previous_result = np.array([], dtype=np.int16)
        
        self.stream = None
        logger.info(f"AudioRecorder initialized: {self.sample_rate}Hz, chunk={self.chunk_size}")

    def start_recording(self):
        """Open audio input stream."""
        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype=np.int16,
                blocksize=self.chunk_size
            )
            self.stream.start()
            logger.info("Audio stream opened successfully")
        except Exception as e:
            logger.error(f"Failed to open audio stream: {e}")
            raise

    def stop_recording(self):
        """Close audio stream."""
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.debug(f"Error closing stream: {e}")
            self.stream = None
            logger.info("Audio stream closed")

    def get_stream(self):
        """Return the underlying stream (for compatibility)."""
        return self.stream

    def get_latest_chunk(self, duration):
        """
        Get latest audio chunk with frame skipping optimization.
        Returns cached result on skipped frames for performance.
        """
        # Frame skipping logic - return cached result on skip frames
        self.frame_counter = (self.frame_counter + 1) % self.frame_skip
        if self.frame_counter != 0:
            return self.previous_result
        
        chunk_size = int(duration * self.sample_rate)
        
        try:
            # sounddevice returns (data, overflowed) tuple
            # data shape is (frames, channels), we flatten since mono
            data, overflowed = self.stream.read(chunk_size)
            if overflowed:
                logger.debug("Audio buffer overflow (non-fatal)")
            self.previous_result = data.flatten().astype(np.int16)
        except Exception as e:
            logger.warning(f"Error reading audio chunk: {e}")
            # Return previous result on error to avoid breaking detection loop
        
        return self.previous_result