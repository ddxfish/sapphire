"""
Tests for audio device fallback logic.

These tests mock sounddevice to verify that the fallback logic
correctly handles various device capability scenarios without
requiring actual audio hardware.

Run with: pytest tests/test_audio_fallbacks.py -v
"""
import pytest
import sys
import numpy as np
from unittest.mock import patch, MagicMock


def get_mock_config():
    """Create a mock config object with test values."""
    mock_cfg = MagicMock()
    mock_cfg.RECORDER_CHUNK_SIZE = 1024
    mock_cfg.RECORDER_CHANNELS = 1
    mock_cfg.RECORDER_SILENCE_THRESHOLD = 0.0025
    mock_cfg.RECORDER_SILENCE_DURATION = 1.0
    mock_cfg.RECORDER_SPEECH_DURATION = 0.2
    mock_cfg.RECORDER_LEVEL_HISTORY_SIZE = 15
    mock_cfg.RECORDER_BACKGROUND_PERCENTILE = 32
    mock_cfg.RECORDER_NOISE_MULTIPLIER = 1.1
    mock_cfg.RECORDER_MAX_SECONDS = 30
    mock_cfg.RECORDER_BEEP_WAIT_TIME = 0.15
    mock_cfg.RECORDER_SAMPLE_RATES = [44100, 48000, 16000, 22050, 32000, 96000, 8000]
    mock_cfg.RECORDER_BLOCKSIZE_FALLBACKS = [1024, 512, 2048, 4096]
    mock_cfg.RECORDER_PREFERRED_DEVICES = ['default']
    mock_cfg.CHUNK_SIZE = 1280
    mock_cfg.FRAME_SKIP = 1
    mock_cfg.BUFFER_DURATION = 0.5
    return mock_cfg


class MockDevice:
    """Mock audio device with configurable capabilities."""
    
    def __init__(self, name, max_input_channels, default_samplerate,
                 supported_rates=None, supported_channels=None, supported_blocksizes=None):
        self.name = name
        self.max_input_channels = max_input_channels
        self.default_samplerate = default_samplerate
        self.supported_rates = supported_rates or [default_samplerate]
        self.supported_channels = supported_channels or [1, 2][:max_input_channels]
        self.supported_blocksizes = supported_blocksizes or [512, 1024, 2048, 4096]
    
    def __getitem__(self, key):
        return getattr(self, key)
    
    def accepts(self, rate, channels, blocksize):
        """Check if device accepts this configuration."""
        return (rate in self.supported_rates and 
                channels in self.supported_channels and
                blocksize in self.supported_blocksizes)


def create_mock_stream_class(device):
    """Create a mock InputStream class that respects device capabilities."""
    class MockInputStream:
        def __init__(self, device=None, samplerate=None, channels=None, dtype=None, blocksize=None):
            if not device_obj.accepts(samplerate, channels, blocksize):
                raise Exception(f"Invalid sample rate: {samplerate}")
            self.samplerate = samplerate
            self.channels = channels
            self.blocksize = blocksize
        
        def close(self):
            pass
        
        def start(self):
            pass
        
        def stop(self):
            pass
        
        def read(self, frames):
            shape = (frames,) if self.channels == 1 else (frames, self.channels)
            return np.zeros(shape, dtype=np.int16), False
    
    device_obj = device
    return MockInputStream


def setup_mocks_and_import_recorder(device):
    """Setup all mocks and import AudioRecorder fresh.
    
    Returns the AudioRecorder class with mocks applied.
    Note: This modifies sys.modules - tests using this should be run
    in isolation or cleanup should happen after.
    """
    # Store original modules for potential restoration
    _original_modules = {
        'config': sys.modules.get('config'),
        'sounddevice': sys.modules.get('sounddevice'),
        'soundfile': sys.modules.get('soundfile'),
    }
    
    # Setup mocks
    mock_sd = MagicMock()
    mock_sd.query_devices = MagicMock(return_value=[device])
    mock_sd.InputStream = create_mock_stream_class(device)
    mock_sd.PortAudioError = Exception  # Mock the exception class
    
    sys.modules['config'] = get_mock_config()
    sys.modules['sounddevice'] = mock_sd
    sys.modules['soundfile'] = MagicMock()
    
    # Mock system_audio to avoid import issues
    mock_sys_audio = MagicMock()
    sys.modules['core.stt.system_audio'] = mock_sys_audio
    
    # Force reimport of recorder module
    for mod in list(sys.modules.keys()):
        if 'core.stt.recorder' in mod:
            del sys.modules[mod]
    
    from core.stt.recorder import AudioRecorder
    return AudioRecorder


def setup_mocks_and_import_wakeword(device):
    """Setup all mocks and import wakeword AudioRecorder fresh."""
    # Setup mocks
    mock_sd = MagicMock()
    mock_sd.query_devices = MagicMock(return_value=[device])
    mock_sd.InputStream = create_mock_stream_class(device)
    
    sys.modules['config'] = get_mock_config()
    sys.modules['sounddevice'] = mock_sd
    
    # Force reimport of wakeword module
    for mod in list(sys.modules.keys()):
        if 'core.wakeword.audio_recorder' in mod:
            del sys.modules[mod]
    
    from core.wakeword.audio_recorder import AudioRecorder
    return AudioRecorder


@pytest.fixture(autouse=True)
def cleanup_sys_modules():
    """Cleanup sys.modules after each test to prevent bleed."""
    yield
    # Remove any mocked modules after test
    mods_to_remove = [k for k in sys.modules.keys() 
                      if k in ('config', 'sounddevice', 'soundfile') 
                      or k.startswith('core.stt') 
                      or k.startswith('core.wakeword')]
    for mod in mods_to_remove:
        if mod in sys.modules:
            del sys.modules[mod]


# =============================================================================
# Sample Rate Fallback Tests
# =============================================================================

class TestSampleRateFallback:
    """Test sample rate fallback logic."""
    
    def test_uses_default_rate_when_supported(self):
        """Device's default rate works - should use it."""
        device = MockDevice(
            name='Test Mic',
            max_input_channels=2,
            default_samplerate=48000,
            supported_rates=[48000, 44100]
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        recorder = AudioRecorder()
        
        assert recorder.device_index == 0
        assert recorder.rate == 48000
    
    def test_falls_back_to_44100(self):
        """Default rate fails, should fall back to 44100."""
        device = MockDevice(
            name='Test Mic',
            max_input_channels=2,
            default_samplerate=48000,
            supported_rates=[44100]  # 48000 not supported
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        recorder = AudioRecorder()
        
        assert recorder.rate == 44100
    
    def test_falls_back_to_96000(self):
        """Pro audio interface only supports 96kHz."""
        device = MockDevice(
            name='Pro Audio Interface',
            max_input_channels=2,
            default_samplerate=96000,
            supported_rates=[96000]  # Only supports 96kHz
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        recorder = AudioRecorder()
        
        assert recorder.rate == 96000
    
    def test_falls_back_to_22050(self):
        """Legacy device only supports 22050Hz."""
        device = MockDevice(
            name='Legacy Mic',
            max_input_channels=1,
            default_samplerate=22050,
            supported_rates=[22050]
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        recorder = AudioRecorder()
        
        assert recorder.rate == 22050


# =============================================================================
# Channel Fallback Tests
# =============================================================================

class TestChannelFallback:
    """Test channel fallback logic (mono -> stereo)."""
    
    def test_uses_mono_when_supported(self):
        """Device supports mono - should use it."""
        device = MockDevice(
            name='Test Mic',
            max_input_channels=2,
            default_samplerate=48000,
            supported_channels=[1, 2]
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        recorder = AudioRecorder()
        
        assert recorder.channels == 1
        assert recorder._needs_stereo_downmix == False
    
    def test_falls_back_to_stereo(self):
        """Device only supports stereo - should use stereo with downmix."""
        device = MockDevice(
            name='Stereo Only Mic',
            max_input_channels=2,
            default_samplerate=48000,
            supported_channels=[2]  # No mono support
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        recorder = AudioRecorder()
        
        assert recorder.channels == 2
        assert recorder._needs_stereo_downmix == True
    
    def test_stereo_downmix_conversion(self):
        """Verify stereo to mono conversion works correctly."""
        device = MockDevice(
            name='Test',
            max_input_channels=2,
            default_samplerate=48000,
            supported_channels=[2]
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        recorder = AudioRecorder()
        
        # Test conversion: stereo with L=100, R=200 should average to 150
        stereo_data = np.array([[100, 200], [300, 400], [500, 600]], dtype=np.int16)
        mono_data = recorder._convert_to_mono(stereo_data)
        
        assert mono_data.shape == (3,)
        assert mono_data[0] == 150  # (100 + 200) / 2
        assert mono_data[1] == 350  # (300 + 400) / 2
        assert mono_data[2] == 550  # (500 + 600) / 2


# =============================================================================
# Blocksize Fallback Tests
# =============================================================================

class TestBlocksizeFallback:
    """Test blocksize fallback logic."""
    
    def test_uses_configured_blocksize(self):
        """Device supports configured blocksize - should use it."""
        device = MockDevice(
            name='Test Mic',
            max_input_channels=2,
            default_samplerate=48000,
            supported_blocksizes=[512, 1024, 2048]
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        recorder = AudioRecorder()
        
        assert recorder.blocksize == 1024
    
    def test_falls_back_to_512(self):
        """Device rejects 1024, should fall back to 512."""
        device = MockDevice(
            name='Small Buffer Mic',
            max_input_channels=2,
            default_samplerate=48000,
            supported_blocksizes=[512]  # Only supports 512
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        recorder = AudioRecorder()
        
        assert recorder.blocksize == 512
    
    def test_falls_back_to_4096(self):
        """Device only supports large buffers."""
        device = MockDevice(
            name='Large Buffer Mic',
            max_input_channels=2,
            default_samplerate=48000,
            supported_blocksizes=[4096]
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        recorder = AudioRecorder()
        
        assert recorder.blocksize == 4096


# =============================================================================
# Error Classification Tests
# =============================================================================

class TestErrorClassification:
    """Test error message classification."""
    
    def _get_classify_func(self):
        """Get classify_audio_error without triggering full module load."""
        # We need a minimal setup just to import the function
        device = MockDevice('Test', 1, 48000)
        sys.modules['config'] = get_mock_config()
        sys.modules['sounddevice'] = MagicMock()
        sys.modules['sounddevice'].query_devices = MagicMock(return_value=[device])
        sys.modules['sounddevice'].InputStream = create_mock_stream_class(device)
        sys.modules['soundfile'] = MagicMock()
        sys.modules['core.stt.system_audio'] = MagicMock()
        
        # Clear cached imports
        for mod in list(sys.modules.keys()):
            if mod.startswith('core.stt'):
                del sys.modules[mod]
        
        from core.stt.recorder import classify_audio_error
        return classify_audio_error
    
    def test_permission_denied_linux(self):
        """Permission denied error gives Linux-specific advice."""
        classify_audio_error = self._get_classify_func()
        
        err = Exception("Permission denied: /dev/snd/pcmC0D0c")
        msg = classify_audio_error(err)
        
        assert 'usermod' in msg
        assert 'audio' in msg
    
    def test_permission_denied_generic(self):
        """EPERM error gives actionable advice."""
        classify_audio_error = self._get_classify_func()
        
        err = Exception("EPERM: operation not permitted")
        msg = classify_audio_error(err)
        
        assert 'denied' in msg.lower()
    
    def test_device_busy(self):
        """Device busy error mentions other apps."""
        classify_audio_error = self._get_classify_func()
        
        err = Exception("Device or resource busy")
        msg = classify_audio_error(err)
        
        assert 'Discord' in msg or 'Zoom' in msg or 'another' in msg.lower()
    
    def test_invalid_sample_rate(self):
        """Sample rate error mentions settings."""
        classify_audio_error = self._get_classify_func()
        
        err = Exception("Invalid sample rate: -9997")
        msg = classify_audio_error(err)
        
        assert 'RECORDER_SAMPLE_RATES' in msg or 'sample rate' in msg.lower()
    
    def test_portaudio_missing(self):
        """Missing PortAudio gives install instructions."""
        classify_audio_error = self._get_classify_func()
        
        err = Exception("PortAudio not initialized")
        msg = classify_audio_error(err)
        
        assert 'apt install' in msg or 'portaudio' in msg.lower()
    
    def test_no_device(self):
        """No device error mentions connection."""
        classify_audio_error = self._get_classify_func()
        
        err = Exception("No such device")
        msg = classify_audio_error(err)
        
        assert 'USB' in msg or 'connection' in msg.lower()
    
    def test_unknown_error_includes_original(self):
        """Unknown errors include original message."""
        classify_audio_error = self._get_classify_func()
        
        err = Exception("Something weird happened with code XYZ123")
        msg = classify_audio_error(err)
        
        assert 'XYZ123' in msg


# =============================================================================
# Wakeword Recorder Tests
# =============================================================================

class TestWakewordRecorder:
    """Test wakeword audio recorder fallback logic."""
    
    def test_prefers_16khz_native(self):
        """Should prefer 16kHz if device supports it (no resampling needed)."""
        device = MockDevice(
            name='Test Mic',
            max_input_channels=2,
            default_samplerate=48000,
            supported_rates=[16000, 48000, 44100]
        )
        
        AudioRecorder = setup_mocks_and_import_wakeword(device)
        recorder = AudioRecorder()
        
        assert recorder.actual_rate == 16000
        assert recorder._resample_ratio == 1.0
    
    def test_resamples_from_48khz(self):
        """Should resample from 48kHz when 16kHz not supported."""
        device = MockDevice(
            name='Test Mic',
            max_input_channels=2,
            default_samplerate=48000,
            supported_rates=[48000]  # No 16kHz
        )
        
        AudioRecorder = setup_mocks_and_import_wakeword(device)
        recorder = AudioRecorder()
        
        assert recorder.actual_rate == 48000
        assert recorder._resample_ratio == 3.0  # 48000 / 16000
    
    def test_stereo_fallback_with_downmix(self):
        """Wakeword recorder should also fall back to stereo."""
        device = MockDevice(
            name='Stereo Only',
            max_input_channels=2,
            default_samplerate=48000,
            supported_channels=[2]
        )
        
        AudioRecorder = setup_mocks_and_import_wakeword(device)
        recorder = AudioRecorder()
        
        assert recorder.channels == 2
        assert recorder._needs_stereo_downmix == True


# =============================================================================
# Combined Fallback Tests
# =============================================================================

class TestCombinedFallbacks:
    """Test multiple fallbacks happening together."""
    
    def test_rate_and_channel_fallback(self):
        """Device needs both rate and channel fallback."""
        device = MockDevice(
            name='Weird Device',
            max_input_channels=2,
            default_samplerate=48000,
            supported_rates=[96000],  # Only 96kHz
            supported_channels=[2]     # Only stereo
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        recorder = AudioRecorder()
        
        assert recorder.rate == 96000
        assert recorder.channels == 2
        assert recorder._needs_stereo_downmix == True
    
    def test_all_three_fallbacks(self):
        """Device needs rate, channel, and blocksize fallback."""
        device = MockDevice(
            name='Very Picky Device',
            max_input_channels=2,
            default_samplerate=48000,
            supported_rates=[22050],
            supported_channels=[2],
            supported_blocksizes=[4096]
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        recorder = AudioRecorder()
        
        assert recorder.rate == 22050
        assert recorder.channels == 2
        assert recorder.blocksize == 4096
        assert recorder._needs_stereo_downmix == True
    
    def test_no_compatible_device_raises(self):
        """Should raise RuntimeError when no device works."""
        device = MockDevice(
            name='Impossible Device',
            max_input_channels=2,
            default_samplerate=48000,
            supported_rates=[99999],  # Impossible rate
            supported_channels=[1],
            supported_blocksizes=[1024]
        )
        
        AudioRecorder = setup_mocks_and_import_recorder(device)
        
        with pytest.raises(RuntimeError) as exc_info:
            AudioRecorder()
        
        assert 'No suitable input device' in str(exc_info.value)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])