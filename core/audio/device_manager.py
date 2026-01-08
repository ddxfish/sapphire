# core/audio/device_manager.py - Audio device discovery and management
"""
Unified audio device management for Sapphire.

Handles device enumeration, capability detection, configuration testing,
and provides a single source of truth for audio device settings used by
both STT recorder and wakeword detection.
"""

import sys
import logging
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

from .errors import classify_audio_error, DeviceNotFoundError, DeviceConfigError

logger = logging.getLogger(__name__)

# Singleton instance
_device_manager: Optional['DeviceManager'] = None


@dataclass
class DeviceInfo:
    """Information about an audio device."""
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float
    is_default_input: bool = False
    is_default_output: bool = False


@dataclass  
class DeviceConfig:
    """Working configuration for a device."""
    device_index: int
    sample_rate: int
    channels: int
    blocksize: int
    needs_stereo_downmix: bool = False
    needs_resampling: bool = False
    resample_ratio: float = 1.0


def get_device_manager() -> 'DeviceManager':
    """Get or create the singleton DeviceManager instance."""
    global _device_manager
    if _device_manager is None:
        _device_manager = DeviceManager()
    return _device_manager


class DeviceManager:
    """
    Manages audio device discovery and configuration.
    
    Provides unified device handling for both STT and wakeword systems,
    with automatic fallback logic for sample rates, channels, and blocksizes.
    """
    
    def __init__(self):
        self._devices_cache: Optional[List[DeviceInfo]] = None
        self._cache_time: float = 0
        self._cache_ttl: float = 5.0  # Refresh device list every 5 seconds max
        
    def _get_settings(self):
        """Lazy import settings to avoid circular imports."""
        import config
        return config
    
    def _get_platform_preferred_devices(self) -> List[str]:
        """Get preferred device names for current platform."""
        config = self._get_settings()
        
        if sys.platform == 'win32':
            return getattr(config, 'AUDIO_PREFERRED_DEVICES_WINDOWS',
                          getattr(config, 'RECORDER_PREFERRED_DEVICES_WINDOWS',
                                 ['default', 'microsoft sound mapper']))
        elif sys.platform == 'linux':
            return getattr(config, 'AUDIO_PREFERRED_DEVICES_LINUX',
                          getattr(config, 'RECORDER_PREFERRED_DEVICES_LINUX',
                                 ['pipewire', 'pulse', 'default']))
        else:
            return getattr(config, 'AUDIO_PREFERRED_DEVICES',
                          getattr(config, 'RECORDER_PREFERRED_DEVICES',
                                 ['default']))
    
    def _get_sample_rates(self) -> List[int]:
        """Get sample rates to try."""
        config = self._get_settings()
        return getattr(config, 'AUDIO_SAMPLE_RATES',
                      getattr(config, 'RECORDER_SAMPLE_RATES',
                             [44100, 48000, 16000, 8000]))
    
    def _get_blocksize_fallbacks(self) -> List[int]:
        """Get blocksizes to try."""
        config = self._get_settings()
        return getattr(config, 'AUDIO_BLOCKSIZE_FALLBACKS',
                      getattr(config, 'RECORDER_BLOCKSIZE_FALLBACKS',
                             [1024, 512, 2048, 4096]))
    
    def _get_configured_input_device(self) -> Optional[int]:
        """Get explicitly configured input device index, if any."""
        config = self._get_settings()
        device = getattr(config, 'AUDIO_INPUT_DEVICE', None)
        if device is not None and device != 'auto':
            try:
                return int(device)
            except (ValueError, TypeError):
                pass
        return None
    
    def query_devices(self, force_refresh: bool = False) -> List[DeviceInfo]:
        """
        Query available audio devices.
        
        Args:
            force_refresh: Bypass cache and query hardware
            
        Returns:
            List of DeviceInfo for all audio devices
        """
        import time
        
        now = time.time()
        if not force_refresh and self._devices_cache and (now - self._cache_time) < self._cache_ttl:
            return self._devices_cache
        
        devices = []
        try:
            raw_devices = sd.query_devices()
            default_input = sd.default.device[0] if isinstance(sd.default.device, tuple) else None
            default_output = sd.default.device[1] if isinstance(sd.default.device, tuple) else None
            
            for i, dev in enumerate(raw_devices):
                devices.append(DeviceInfo(
                    index=i,
                    name=dev['name'],
                    max_input_channels=dev['max_input_channels'],
                    max_output_channels=dev['max_output_channels'],
                    default_samplerate=dev['default_samplerate'],
                    is_default_input=(i == default_input),
                    is_default_output=(i == default_output),
                ))
        except Exception as e:
            logger.error(f"Failed to query audio devices: {classify_audio_error(e)}")
            return []
        
        self._devices_cache = devices
        self._cache_time = now
        return devices
    
    def get_input_devices(self) -> List[DeviceInfo]:
        """Get all devices capable of audio input."""
        return [d for d in self.query_devices() if d.max_input_channels > 0]
    
    def get_output_devices(self) -> List[DeviceInfo]:
        """Get all devices capable of audio output."""
        return [d for d in self.query_devices() if d.max_output_channels > 0]
    
    def get_device_help(self) -> str:
        """Generate helpful message about available input devices."""
        input_devs = self.get_input_devices()
        if input_devs:
            lines = [f"  [{d.index}] {d.name}" for d in input_devs]
            return "Available input devices:\n" + "\n".join(lines)
        return "No input devices detected. Check audio drivers and connections."
    
    def test_device_config(self, device_index: int, sample_rate: int, 
                           channels: int, blocksize: int) -> bool:
        """
        Test if a device supports the given configuration.
        
        Args:
            device_index: Device index to test
            sample_rate: Sample rate in Hz
            channels: Number of channels
            blocksize: Buffer size in samples
            
        Returns:
            True if configuration is supported
        """
        try:
            stream = sd.InputStream(
                device=device_index,
                samplerate=sample_rate,
                channels=channels,
                dtype=np.int16,
                blocksize=blocksize
            )
            stream.close()
            logger.debug(f"  OK: device={device_index}, rate={sample_rate}, "
                        f"ch={channels}, block={blocksize}")
            return True
        except Exception as e:
            logger.debug(f"  FAIL: device={device_index}, rate={sample_rate}, "
                        f"ch={channels}, block={blocksize}: {e}")
            return False
    
    def find_working_config(self, device_index: int, dev_info: DeviceInfo,
                           target_rate: Optional[int] = None,
                           preferred_blocksize: Optional[int] = None) -> Optional[DeviceConfig]:
        """
        Find a working configuration for a device with fallbacks.
        
        Tries configurations in priority order:
        1. Target rate (if specified) + mono + preferred blocksize
        2. Device default rate + mono + fallback blocksizes
        3. Fallback rates + mono + all blocksizes
        4. All above with stereo (if device supports it)
        
        Args:
            device_index: Device to configure
            dev_info: Device information
            target_rate: Preferred sample rate (e.g., 16000 for wakeword)
            preferred_blocksize: Preferred buffer size
            
        Returns:
            DeviceConfig if successful, None if all configurations failed
        """
        default_rate = int(dev_info.default_samplerate)
        max_channels = dev_info.max_input_channels
        
        # Build rate list
        fallback_rates = self._get_sample_rates()
        sample_rates = []
        if target_rate:
            sample_rates.append(target_rate)
        sample_rates.append(default_rate)
        sample_rates.extend([r for r in fallback_rates if r not in sample_rates])
        
        # Build blocksize list
        blocksizes = self._get_blocksize_fallbacks()
        if preferred_blocksize and preferred_blocksize not in blocksizes:
            blocksizes = [preferred_blocksize] + blocksizes
        
        # Channel options: prefer mono, fall back to stereo
        channel_options = [1]
        if max_channels >= 2:
            channel_options.append(2)
        
        # Try all combinations
        for rate in sample_rates:
            for channels in channel_options:
                for blocksize in blocksizes:
                    if self.test_device_config(device_index, rate, channels, blocksize):
                        needs_resample = target_rate and rate != target_rate
                        resample_ratio = rate / target_rate if target_rate else 1.0
                        
                        return DeviceConfig(
                            device_index=device_index,
                            sample_rate=rate,
                            channels=channels,
                            blocksize=blocksize,
                            needs_stereo_downmix=(channels == 2),
                            needs_resampling=bool(needs_resample),
                            resample_ratio=resample_ratio,
                        )
        
        return None
    
    def find_input_device(self, target_rate: Optional[int] = None,
                         preferred_blocksize: Optional[int] = None) -> DeviceConfig:
        """
        Find a working input device with automatic fallbacks.
        
        Priority order:
        1. Explicitly configured device (AUDIO_INPUT_DEVICE setting)
        2. Platform-preferred devices (pipewire, pulse, etc.)
        3. Any available input device
        
        Args:
            target_rate: Preferred sample rate (e.g., 16000 for wakeword)
            preferred_blocksize: Preferred buffer size
            
        Returns:
            DeviceConfig for the selected device
            
        Raises:
            DeviceNotFoundError: If no input device could be found
            DeviceConfigError: If device found but configuration failed
        """
        input_devices = self.get_input_devices()
        
        if not input_devices:
            raise DeviceNotFoundError(
                "No input devices found. Check microphone connection.\n" +
                self.get_device_help()
            )
        
        # Log available devices
        for dev in input_devices:
            logger.debug(f"Found input device {dev.index}: {dev.name} "
                        f"(max_ch={dev.max_input_channels}, "
                        f"default_rate={dev.default_samplerate})")
        
        # Try explicitly configured device first
        configured_idx = self._get_configured_input_device()
        if configured_idx is not None:
            dev = next((d for d in input_devices if d.index == configured_idx), None)
            if dev:
                logger.info(f"Trying configured device: {dev.name}")
                config = self.find_working_config(
                    dev.index, dev, target_rate, preferred_blocksize
                )
                if config:
                    logger.info(f"Using configured device {dev.index}: {dev.name}")
                    return config
                logger.warning(f"Configured device {configured_idx} failed, trying others")
        
        # Try platform-preferred devices
        preferred = self._get_platform_preferred_devices()
        for pref_name in preferred:
            for dev in input_devices:
                if pref_name.lower() in dev.name.lower():
                    logger.info(f"Trying preferred device: {dev.name}")
                    config = self.find_working_config(
                        dev.index, dev, target_rate, preferred_blocksize
                    )
                    if config:
                        logger.info(f"Selected preferred device {dev.index}: {dev.name}")
                        return config
        
        # Fall back to any available device
        for dev in input_devices:
            logger.info(f"Trying device: {dev.name}")
            config = self.find_working_config(
                dev.index, dev, target_rate, preferred_blocksize
            )
            if config:
                logger.info(f"Selected fallback device {dev.index}: {dev.name}")
                return config
        
        raise DeviceConfigError(
            "All input devices failed configuration.\n" +
            self.get_device_help()
        )
    
    def test_input_device(self, device_index: Optional[int] = None,
                         duration: float = 0.5) -> Dict[str, Any]:
        """
        Test an input device by recording a short sample.
        
        Args:
            device_index: Device to test, or None for default
            duration: Recording duration in seconds
            
        Returns:
            Dict with 'success', 'peak_level', 'device_name', 'error'
        """
        try:
            devices = self.query_devices()
            
            if device_index is None:
                # Use first available input device
                input_devs = [d for d in devices if d.max_input_channels > 0]
                if not input_devs:
                    return {'success': False, 'error': 'No input devices available'}
                device_index = input_devs[0].index
            
            dev = next((d for d in devices if d.index == device_index), None)
            if not dev:
                return {'success': False, 'error': f'Device {device_index} not found'}
            
            if dev.max_input_channels < 1:
                return {'success': False, 'error': f'{dev.name} has no input channels'}
            
            # Find working config
            config = self.find_working_config(device_index, dev)
            if not config:
                return {'success': False, 'error': f'Could not configure {dev.name}'}
            
            # Record short sample
            samples = int(duration * config.sample_rate)
            recording = sd.rec(
                samples,
                samplerate=config.sample_rate,
                channels=config.channels,
                dtype=np.int16,
                device=device_index
            )
            sd.wait()
            
            # Calculate peak level
            from .utils import calculate_peak, convert_to_mono
            mono = convert_to_mono(recording) if config.needs_stereo_downmix else recording.flatten()
            peak = calculate_peak(mono)
            
            return {
                'success': True,
                'peak_level': peak,
                'device_name': dev.name,
                'sample_rate': config.sample_rate,
                'channels': config.channels,
            }
            
        except Exception as e:
            return {'success': False, 'error': classify_audio_error(e)}