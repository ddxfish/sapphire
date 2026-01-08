// tabs/stt.js - Speech-to-text settings

export default {
  id: 'stt',
  name: 'STT',
  icon: '🎤',
  description: 'Speech-to-text engine and voice detection settings',
  keys: [
    'STT_ENABLED',
    'STT_ENGINE',
    'STT_MODEL_SIZE',
    'STT_LANGUAGE',
    'STT_HOST',
    'STT_SERVER_PORT',
    'FASTER_WHISPER_CUDA_DEVICE',
    'FASTER_WHISPER_DEVICE',
    'FASTER_WHISPER_COMPUTE_TYPE',
    'FASTER_WHISPER_BEAM_SIZE',
    'FASTER_WHISPER_NUM_WORKERS',
    'FASTER_WHISPER_VAD_FILTER',
    'FASTER_WHISPER_VAD_PARAMETERS',
    'RECORDER_CHUNK_SIZE',
    'RECORDER_CHANNELS',
    'RECORDER_SILENCE_THRESHOLD',
    'RECORDER_SILENCE_DURATION',
    'RECORDER_SPEECH_DURATION',
    'RECORDER_LEVEL_HISTORY_SIZE',
    'RECORDER_BACKGROUND_PERCENTILE',
    'RECORDER_NOISE_MULTIPLIER',
    'RECORDER_MAX_SECONDS',
    'RECORDER_BEEP_WAIT_TIME'
  ],

  render(modal) {
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(this.keys)}
      </div>
    `;
  }
};