// tabs/tts.js - Text-to-speech settings

export default {
  id: 'tts',
  name: 'TTS',
  icon: '🔊',
  description: 'Text-to-speech settings',
  keys: [
    'TTS_ENABLED',
    'TTS_VOICE_NAME',
    'TTS_SPEED',
    'TTS_PITCH_SHIFT',
    'TTS_SERVER_HOST',
    'TTS_SERVER_PORT',
    'TTS_PRIMARY_SERVER',
    'TTS_FALLBACK_SERVER',
    'TTS_FALLBACK_TIMEOUT'
  ],

  render(modal) {
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(this.keys)}
      </div>
    `;
  }
};