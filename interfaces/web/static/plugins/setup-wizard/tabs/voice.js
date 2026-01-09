// tabs/voice.js - Voice feature setup (TTS, STT, Wakeword)

import { checkPackages, updateSetting } from '../setup-api.js';

let packageStatus = {};

export default {
  id: 'voice',
  name: 'Voice',
  icon: '🗣️',

  async render(settings) {
    // Check installed packages
    try {
      packageStatus = await checkPackages();
    } catch (e) {
      console.warn('Could not check packages:', e);
      packageStatus = {};
    }

    const ttsEnabled = settings.TTS_ENABLED || false;
    const sttEnabled = settings.STT_ENABLED || false;
    const wakewordEnabled = settings.WAKE_WORD_ENABLED || false;

    const ttsInstalled = packageStatus.tts?.installed || false;
    const sttInstalled = packageStatus.stt?.installed || false;
    const wakewordInstalled = packageStatus.wakeword?.installed || false;

    return `
      <div class="setup-tab-header">
        <h3>🗣️ How should Sapphire communicate?</h3>
        <p>Choose how you want to interact with your AI assistant</p>
      </div>

      <div class="help-tip">
        <span class="tip-icon">💡</span>
        <span>Each feature is optional. You can always chat by typing, but voice makes it feel more natural!</span>
      </div>

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
        ${this.renderPackageStatus('stt', sttInstalled)}
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
        ${this.renderPackageStatus('tts', ttsInstalled)}
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
        ${this.renderPackageStatus('wakeword', wakewordInstalled)}
      </div>

      <div class="help-tip">
        <span class="tip-icon">ℹ️</span>
        <span>Don't worry if packages aren't installed yet - you can enable these features later in Settings.</span>
      </div>
    `;
  },

  renderPackageStatus(key, installed) {
    const pkg = packageStatus[key] || {};
    const requirements = pkg.requirements || `requirements-${key}.txt`;

    if (installed) {
      return `
        <div class="package-status installed">
          ✓ ${pkg.package || key} is installed and ready to use
        </div>
      `;
    }

    return `
      <div class="package-status not-installed">
        <div>⚠️ Requires ${pkg.package || key} - install to enable this feature:</div>
        <div class="pip-command">
          <code>pip install -r ${requirements}</code>
          <button class="copy-btn" data-copy="pip install -r ${requirements}">Copy</button>
        </div>
      </div>
    `;
  },

  attachListeners(container, settings, updateSettings) {
    // Feature toggles
    container.querySelectorAll('.feature-toggle input').forEach(toggle => {
      toggle.addEventListener('change', async (e) => {
        const settingKey = e.target.dataset.setting;
        const enabled = e.target.checked;
        const card = e.target.closest('.feature-card');

        // Update setting
        try {
          await updateSetting(settingKey, enabled);
          settings[settingKey] = enabled;

          // Update card visual state
          if (enabled) {
            card.classList.add('enabled');
          } else {
            card.classList.remove('enabled');
          }
        } catch (err) {
          console.error('Failed to update setting:', err);
          e.target.checked = !enabled; // Revert
        }
      });
    });

    // Copy buttons
    container.querySelectorAll('.copy-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const text = btn.dataset.copy;
        navigator.clipboard.writeText(text).then(() => {
          const original = btn.textContent;
          btn.textContent = 'Copied!';
          setTimeout(() => btn.textContent = original, 1500);
        });
      });
    });
  },

  validate(settings) {
    // Voice tab is always valid - features are optional
    return { valid: true };
  }
};