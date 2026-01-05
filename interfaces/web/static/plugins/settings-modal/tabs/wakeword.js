// tabs/wakeword.js - Wake word detection and audio recording

export default {
  id: 'wakeword',
  name: 'Wakeword',
  icon: '🎵',
  description: 'Wake word detection and audio recording',
  keys: [
    'WAKE_WORD_ENABLED',
    'WAKEWORD_MODEL',
    'WAKEWORD_THRESHOLD',
    'WAKEWORD_FRAMEWORK',
    'WAKEWORD_DEVICE_INDEX',
    'CHUNK_SIZE',
    'BUFFER_DURATION',
    'FRAME_SKIP',
    'RECORDING_SAMPLE_RATE',
    'PLAYBACK_SAMPLE_RATE',
    'WAKE_TONE_DURATION',
    'WAKE_TONE_FREQUENCY',
    'CALLBACK_THREAD_POOL_SIZE',
    'RECORDER_CHUNK_SIZE',
    'RECORDER_CHANNELS',
    'RECORDER_SILENCE_THRESHOLD',
    'RECORDER_SILENCE_DURATION',
    'RECORDER_SPEECH_DURATION',
    'RECORDER_LEVEL_HISTORY_SIZE',
    'RECORDER_BACKGROUND_PERCENTILE',
    'RECORDER_NOISE_MULTIPLIER',
    'RECORDER_MAX_SECONDS',
    'RECORDER_BEEP_WAIT_TIME',
    'RECORDER_SAMPLE_RATES',
    'RECORDER_PREFERRED_DEVICES_LINUX',
    'RECORDER_PREFERRED_DEVICES_WINDOWS'
  ],

  render(modal) {
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(this.keys)}
      </div>
    `;
  }
};