// tabs/stt.js - Speech-to-text settings

export default {
  id: 'stt',
  name: 'STT',
  icon: '🎤',
  description: 'Speech-to-text settings',
  keys: [
    'STT_ENABLED',
    'STT_ENGINE',
    'STT_MODEL_SIZE',
    'STT_LANGUAGE',
    'STT_HOST',
    'STT_SERVER_PORT',
    'TRANSCRIBE_SAMPLE_RATE',
    'TRANSCRIBE_CHANNELS',
    'FASTER_WHISPER_CUDA_DEVICE',
    'FASTER_WHISPER_DEVICE',
    'FASTER_WHISPER_COMPUTE_TYPE',
    'FASTER_WHISPER_BEAM_SIZE',
    'FASTER_WHISPER_NUM_WORKERS',
    'FASTER_WHISPER_VAD_FILTER',
    'FASTER_WHISPER_VAD_PARAMETERS'
  ],

  render(modal) {
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(this.keys)}
      </div>
    `;
  }
};