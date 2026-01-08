// tabs/wakeword.js - Wake word detection settings

export default {
  id: 'wakeword',
  name: 'Wakeword',
  icon: '🎵',
  description: 'Wake word detection settings',
  keys: [
    'WAKE_WORD_ENABLED',
    'WAKEWORD_MODEL',
    'WAKEWORD_THRESHOLD',
    'WAKEWORD_FRAMEWORK',
    'CHUNK_SIZE',
    'BUFFER_DURATION',
    'FRAME_SKIP',
    'PLAYBACK_SAMPLE_RATE',
    'WAKE_TONE_DURATION',
    'WAKE_TONE_FREQUENCY',
    'CALLBACK_THREAD_POOL_SIZE'
  ],

  render(modal) {
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(this.keys)}
      </div>
    `;
  }
};