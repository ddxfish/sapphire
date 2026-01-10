// tabs/voice.js - Voice feature setup (TTS, STT, Wakeword)

import { checkPackages, updateSetting } from '../setup-api.js';

let packageStatus = {};

export default {
  id: 'voice',
  name: 'Voice',
  icon: '🗣️',

  async render(settings) {
    const ttsEnabled = settings.TTS_ENABLED || false;
    const sttEnabled = settings.STT_ENABLED || false;
    const wakewordEnabled = settings.WAKE_WORD_ENABLED || false;

    return `
      <!-- Speech Recognition (STT) -->
      <div class="feature-card ${sttEnabled ? 'enabled' : ''}" data-feature="stt">
        <div class="feature-card-header">
          <span class="feature-icon">🎤</span>
          <div class="feature-info">
            <h4>Speech Recognition</h4>
            <p>Talk to Sapphire using your voice</p>
          </div>
          <label class="feature-toggle">
            <input type="checkbox" data-setting="STT_ENABLED" ${sttEnabled ? 'checked' : ''}>
            <span class="slider"></span>
          </label>
        </div>
        <div class="package-status checking" data-package="stt">
          <span class="spinner">⏳</span> Checking Faster Whisper...
        </div>
      </div>

      <!-- Voice Responses (TTS) -->
      <div class="feature-card ${ttsEnabled ? 'enabled' : ''}" data-feature="tts">
        <div class="feature-card-header">
          <span class="feature-icon">🔊</span>
          <div class="feature-info">
            <h4>Voice Responses</h4>
            <p>Sapphire speaks back to you</p>
          </div>
          <label class="feature-toggle">
            <input type="checkbox" data-setting="TTS_ENABLED" ${ttsEnabled ? 'checked' : ''}>
            <span class="slider"></span>
          </label>
        </div>
        <div class="package-status checking" data-package="tts">
          <span class="spinner">⏳</span> Checking Kokoro TTS...
        </div>
      </div>

      <!-- Wake Word -->
      <div class="feature-card ${wakewordEnabled ? 'enabled' : ''}" data-feature="wakeword">
        <div class="feature-card-header">
          <span class="feature-icon">🎵</span>
          <div class="feature-info">
            <h4>Wake Word</h4>
            <p>Say "Hey Sapphire" to start talking anytime</p>
          </div>
          <label class="feature-toggle">
            <input type="checkbox" data-setting="WAKE_WORD_ENABLED" ${wakewordEnabled ? 'checked' : ''}>
            <span class="slider"></span>
          </label>
        </div>
        <div class="package-status checking" data-package="wakeword">
          <span class="spinner">⏳</span> Checking OpenWakeWord...
        </div>
      </div>
    `;
  },

  attachListeners(container, settings, updateSettings) {
    // Start package check async - updates DOM when complete
    this.loadPackageStatus(container);

    // Feature toggles
    container.querySelectorAll('.feature-toggle input').forEach(toggle => {
      toggle.addEventListener('change', async (e) => {
        const settingKey = e.target.dataset.setting;
        const enabled = e.target.checked;
        const card = e.target.closest('.feature-card');

        try {
          await updateSetting(settingKey, enabled);
          settings[settingKey] = enabled;

          if (enabled) {
            card.classList.add('enabled');
          } else {
            card.classList.remove('enabled');
          }
        } catch (err) {
          console.error('Failed to update setting:', err);
          e.target.checked = !enabled;
        }
      });
    });

    // Copy buttons (delegated - works for dynamically added buttons)
    container.addEventListener('click', (e) => {
      if (e.target.classList.contains('copy-btn')) {
        e.stopPropagation();
        const text = e.target.dataset.copy;
        navigator.clipboard.writeText(text).then(() => {
          const original = e.target.textContent;
          e.target.textContent = 'Copied!';
          setTimeout(() => e.target.textContent = original, 1500);
        });
      }
    });
  },

  async loadPackageStatus(container) {
    try {
      packageStatus = await checkPackages();

      // Update each package status in DOM
      for (const [key, info] of Object.entries(packageStatus)) {
        const statusEl = container.querySelector(`[data-package="${key}"]`);
        if (!statusEl) continue;

        statusEl.classList.remove('checking');

        if (info.installed) {
          statusEl.classList.add('installed');
          statusEl.innerHTML = `✓ ${info.package || key} is installed and ready to use`;
        } else {
          statusEl.classList.add('not-installed');
          const requirements = info.requirements || `requirements-${key}.txt`;
          statusEl.innerHTML = `
            <div>⚠️ Requires ${info.package || key} - install to enable this feature:</div>
            <div class="pip-command">
              <code>pip install -r ${requirements}</code>
              <button class="copy-btn" data-copy="pip install -r ${requirements}">Copy</button>
            </div>
          `;
        }
      }
    } catch (e) {
      console.warn('Could not check packages:', e);
      // Show error state
      container.querySelectorAll('.package-status.checking').forEach(el => {
        el.classList.remove('checking');
        el.innerHTML = `⚠️ Could not check package status`;
      });
    }
  },

  validate(settings) {
    return { valid: true };
  }
};