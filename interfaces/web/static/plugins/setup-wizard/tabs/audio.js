// tabs/audio.js - Audio device setup

import {
  populateDeviceSelects,
  attachAudioDeviceListeners,
  runInputTest,
  runOutputTest
} from '../../../shared/audio-devices.js';

export default {
  id: 'audio',
  name: 'Audio',
  icon: '🎧',

  render(settings) {
    return `
      <!-- Microphone -->
      <div class="audio-section">
        <h4>🎤 Microphone</h4>
        <div class="device-row">
          <select id="setup-audio-input" class="device-select">
            <option value="auto">Auto-detect (recommended)</option>
          </select>
          <button class="btn btn-secondary" data-test="input">
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

      <!-- Speakers -->
      <div class="audio-section">
        <h4>🔊 Speakers</h4>
        <div class="device-row">
          <select id="setup-audio-output" class="device-select">
            <option value="auto">System default</option>
          </select>
          <button class="btn btn-secondary" data-test="output">
            <span class="btn-icon">🔊</span> Test
          </button>
        </div>
        <div class="test-result" data-result="output"></div>
      </div>
    `;
  },

  async attachListeners(container, settings, updateSettings) {
    // Load and populate device lists
    try {
      await populateDeviceSelects(container, 'setup-audio-input', 'setup-audio-output');
    } catch (e) {
      const inputResult = container.querySelector('[data-result="input"]');
      if (inputResult) {
        inputResult.textContent = `⚠️ Could not load devices: ${e.message}`;
        inputResult.className = 'test-result error';
      }
    }

    // Device change handlers
    attachAudioDeviceListeners(container, {
      inputSelectId: 'setup-audio-input',
      outputSelectId: 'setup-audio-output',
      onInputChange: (value) => {
        settings.AUDIO_INPUT_DEVICE = value;
      },
      onOutputChange: (value) => {
        settings.AUDIO_OUTPUT_DEVICE = value;
      }
    });
  },

  validate(settings) {
    // Audio setup is always valid - defaults work fine
    return { valid: true };
  }
};