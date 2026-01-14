"""
Tests for audio error classification.

The error classification function is a pure function that doesn't require
actual audio hardware, so these tests can run anywhere.

DeviceManager tests are skipped when sounddevice/portaudio is unavailable.

Run with: pytest tests/test_audio_fallbacks.py -v
"""
import pytest
import sys
from unittest.mock import MagicMock


# =============================================================================
# Check if sounddevice is available
# =============================================================================

def sounddevice_available():
    """Check if sounddevice can be imported (has PortAudio)."""
    try:
        import sounddevice
        return True
    except (ImportError, OSError):
        return False


# Mock sounddevice BEFORE any core.audio imports if not available
if not sounddevice_available():
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = []
    mock_sd.default.device = (0, 0)
    sys.modules['sounddevice'] = mock_sd


# =============================================================================
# Error Classification Tests (Pure Function - No Hardware Required)
# =============================================================================

class TestErrorClassification:
    """Test error message classification - pure function, no hardware needed."""
    
    def test_permission_denied_linux(self):
        """Permission denied error gives Linux-specific advice."""
        from core.audio.errors import classify_audio_error
        
        err = Exception("Permission denied: /dev/snd/pcmC0D0c")
        msg = classify_audio_error(err)
        
        assert 'usermod' in msg
        assert 'audio' in msg
    
    def test_permission_denied_generic(self):
        """EPERM error gives actionable advice."""
        from core.audio.errors import classify_audio_error
        
        err = Exception("EPERM: operation not permitted")
        msg = classify_audio_error(err)
        
        assert 'denied' in msg.lower()
    
    def test_device_busy(self):
        """Device busy error mentions other apps."""
        from core.audio.errors import classify_audio_error
        
        err = Exception("Device or resource busy")
        msg = classify_audio_error(err)
        
        assert 'Discord' in msg or 'Zoom' in msg or 'another' in msg.lower()
    
    def test_invalid_sample_rate(self):
        """Sample rate error mentions settings."""
        from core.audio.errors import classify_audio_error
        
        err = Exception("Invalid sample rate: -9997")
        msg = classify_audio_error(err)
        
        assert 'AUDIO_SAMPLE_RATES' in msg or 'sample rate' in msg.lower()
    
    def test_portaudio_missing(self):
        """Missing PortAudio gives install instructions."""
        from core.audio.errors import classify_audio_error
        
        err = Exception("PortAudio not initialized")
        msg = classify_audio_error(err)
        
        assert 'apt install' in msg or 'portaudio' in msg.lower()
    
    def test_no_device(self):
        """No device error mentions connection."""
        from core.audio.errors import classify_audio_error
        
        err = Exception("No such device")
        msg = classify_audio_error(err)
        
        assert 'USB' in msg or 'connection' in msg.lower()
    
    def test_unknown_error_includes_original(self):
        """Unknown errors include original message."""
        from core.audio.errors import classify_audio_error
        
        err = Exception("Something weird happened with code XYZ123")
        msg = classify_audio_error(err)
        
        assert 'XYZ123' in msg
    
    def test_timeout_error(self):
        """Timeout/underrun error gives buffer advice."""
        from core.audio.errors import classify_audio_error
        
        err = Exception("Audio buffer underrun detected")
        msg = classify_audio_error(err)
        
        assert 'buffer' in msg.lower()
    
    def test_channel_error(self):
        """Channel count error is classified."""
        from core.audio.errors import classify_audio_error
        
        err = Exception("Invalid number of channels: -9998")
        msg = classify_audio_error(err)
        
        assert 'channel' in msg.lower()


# =============================================================================
# DeviceManager Tests - Require working sounddevice
# =============================================================================

@pytest.mark.skipif(not sounddevice_available(), 
                    reason="sounddevice/PortAudio not available")
class TestDeviceManagerWithHardware:
    """
    These tests require actual sounddevice with PortAudio.
    They're skipped in CI environments without audio hardware.
    """
    
    def test_query_devices_returns_list(self):
        """query_devices should return a list."""
        from core.audio.device_manager import get_device_manager
        dm = get_device_manager()
        devices = dm.query_devices()
        assert isinstance(devices, list)
    
    def test_get_device_help_returns_string(self):
        """get_device_help should return helpful text."""
        from core.audio.device_manager import get_device_manager
        dm = get_device_manager()
        help_text = dm.get_device_help()
        assert isinstance(help_text, str)
        assert len(help_text) > 0


# =============================================================================
# DeviceInfo/DeviceConfig Dataclass Tests
# =============================================================================

class TestDeviceDataclasses:
    """Test dataclass structures don't require hardware."""
    
    def test_device_info_fields(self):
        """DeviceInfo should have expected fields."""
        from core.audio.device_manager import DeviceInfo
        
        info = DeviceInfo(
            index=0,
            name="Test Device",
            max_input_channels=2,
            max_output_channels=2,
            default_samplerate=48000.0
        )
        
        assert info.index == 0
        assert info.name == "Test Device"
        assert info.max_input_channels == 2
    
    def test_device_config_fields(self):
        """DeviceConfig should have expected fields."""
        from core.audio.device_manager import DeviceConfig
        
        config = DeviceConfig(
            device_index=0,
            sample_rate=48000,
            channels=1,
            blocksize=1024,
            needs_stereo_downmix=False,
            needs_resampling=False,
            resample_ratio=1.0
        )
        
        assert config.device_index == 0
        assert config.sample_rate == 48000
        assert config.channels == 1
        assert config.blocksize == 1024
        assert config.needs_stereo_downmix is False
    
    def test_device_config_stereo_downmix(self):
        """DeviceConfig should track stereo downmix flag."""
        from core.audio.device_manager import DeviceConfig
        
        config = DeviceConfig(
            device_index=0,
            sample_rate=48000,
            channels=2,
            blocksize=1024,
            needs_stereo_downmix=True
        )
        
        assert config.needs_stereo_downmix is True
    
    def test_device_config_resampling(self):
        """DeviceConfig should track resampling info."""
        from core.audio.device_manager import DeviceConfig
        
        config = DeviceConfig(
            device_index=0,
            sample_rate=48000,
            channels=1,
            blocksize=1024,
            needs_resampling=True,
            resample_ratio=3.0  # 48000 / 16000
        )
        
        assert config.needs_resampling is True
        assert config.resample_ratio == 3.0


# =============================================================================
# Audio Utils Tests
# =============================================================================

class TestAudioUtils:
    """Test audio utility functions."""
    
    def test_convert_to_mono_stereo_input(self):
        """convert_to_mono should average stereo channels."""
        import numpy as np
        from core.audio.utils import convert_to_mono
        
        # Stereo: left=100, right=200 -> mono=150
        stereo = np.array([[100, 200], [300, 400]], dtype=np.int16)
        mono = convert_to_mono(stereo)
        
        assert mono.shape == (2,)
        assert mono[0] == 150  # (100+200)/2
        assert mono[1] == 350  # (300+400)/2
    
    def test_convert_to_mono_already_mono(self):
        """convert_to_mono should handle already-mono input."""
        import numpy as np
        from core.audio.utils import convert_to_mono
        
        mono_in = np.array([100, 200, 300], dtype=np.int16)
        mono_out = convert_to_mono(mono_in)
        
        # Should just flatten
        assert mono_out.shape == (3,)
    
    def test_get_temp_dir_returns_path(self):
        """get_temp_dir should return a valid path."""
        from core.audio.utils import get_temp_dir
        import os
        
        temp_dir = get_temp_dir()
        assert isinstance(temp_dir, str)
        assert os.path.isdir(temp_dir)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])