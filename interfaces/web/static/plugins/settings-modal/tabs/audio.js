// tabs/audio.js - Audio device configuration
// Uses shared/audio-devices.js for reusable components

import {
  populateDeviceSelects,
  attachAudioDeviceListeners
} from '../../../shared/audio-devices.js';

export default {
  id: 'audio',
  name: 'Audio',
  icon: '🎧',
  description: 'Audio device selection and testing',
  keys: [
    'AUDIO_INPUT_DEVICE',
    'AUDIO_OUTPUT_DEVICE',
    'AUDIO_SAMPLE_RATES',
    'AUDIO_BLOCKSIZE_FALLBACKS',
    'AUDIO_PREFERRED_DEVICES_LINUX',
    'AUDIO_PREFERRED_DEVICES_WINDOWS'
  ],

  // Advanced settings hidden in accordion
  advancedKeys: [
    'AUDIO_SAMPLE_RATES',
    'AUDIO_BLOCKSIZE_FALLBACKS',
    'AUDIO_PREFERRED_DEVICES_LINUX',
    'AUDIO_PREFERRED_DEVICES_WINDOWS'
  ],

  render(modal) {
    return `
      <div class="audio-settings">
        <div class="audio-devices-section">
          <h4>Input Device (Microphone)</h4>
          <p class="section-desc">Select microphone for voice input. Use "Auto-detect" for automatic selection.</p>
          <div class="device-row">
            <select id="audio-input-select" class="device-select">
              <option value="auto">Auto-detect (recommended)</option>
            </select>
            <button class="btn btn-sm btn-test" data-test="input">
              <span class="btn-icon">🎤</span> Test
            </button>
          </div>
          <div class="test-result" data-result="input"></div>
          <div class="level-meter-container" data-meter="input" style="display:none;">
            <div class="level-meter">
              <div class="level-bar" data-bar="input"></div>
            </div>
            <span class="level-value" data-value="input">0%</span>
          </div>
        </div>

        <div class="audio-devices-section">
          <h4>Output Device (Speakers)</h4>
          <p class="section-desc">Select speakers/headphones for TTS playback. Use "System default" for automatic selection.</p>
          <div class="device-row">
            <select id="audio-output-select" class="device-select">
              <option value="auto">System default</option>
            </select>
            <button class="btn btn-sm btn-test" data-test="output">
              <span class="btn-icon">🔊</span> Test
            </button>
          </div>
          <div class="test-result" data-result="output"></div>
        </div>

        ${modal.renderAdvancedAccordion('audio-advanced', this.advancedKeys)}
      </div>
    `;
  },

  async attachListeners(modal, container) {
    // Load and populate devices
    try {
      await populateDeviceSelects(container);
    } catch (e) {
      const inputResult = container.querySelector('[data-result="input"]');
      if (inputResult) {
        inputResult.textContent = `⚠ Could not load devices: ${e.message}`;
        inputResult.className = 'test-result error';
      }
    }

    // Attach device change and test listeners
    attachAudioDeviceListeners(container, {
      onInputChange: (value) => {
        modal.settings.AUDIO_INPUT_DEVICE = value;
        modal.pendingChanges.AUDIO_INPUT_DEVICE = value;
      },
      onOutputChange: (value) => {
        modal.settings.AUDIO_OUTPUT_DEVICE = value;
        modal.pendingChanges.AUDIO_OUTPUT_DEVICE = value;
      }
    });

    // Attach accordion toggle listeners (uses DRY helper)
    modal.attachAccordionListeners(container);
  }
};