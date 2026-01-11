// tabs/stt.js - Speech-to-text settings (simplified)
// Essential settings visible by default, advanced in accordion

export default {
  id: 'stt',
  name: 'STT',
  icon: '🎤',
  description: 'Speech-to-text engine and voice detection settings',
  
  // All keys this tab manages (for settings system)
  keys: [
    'STT_ENABLED',
    'STT_MODEL_SIZE',
    'STT_HOST',
    'STT_SERVER_PORT',
    'FASTER_WHISPER_DEVICE',
    'FASTER_WHISPER_CUDA_DEVICE',
    'FASTER_WHISPER_COMPUTE_TYPE',
    'FASTER_WHISPER_BEAM_SIZE',
    'FASTER_WHISPER_NUM_WORKERS',
    'FASTER_WHISPER_VAD_FILTER',
    'RECORDER_SILENCE_THRESHOLD',
    'RECORDER_SILENCE_DURATION',
    'RECORDER_SPEECH_DURATION',
    'RECORDER_BACKGROUND_PERCENTILE',
    'RECORDER_MAX_SECONDS',
    'RECORDER_BEEP_WAIT_TIME'
  ],

  // Essential - what 90% of users need
  essentialKeys: [
    'STT_ENABLED',
    'STT_MODEL_SIZE',
    'RECORDER_BACKGROUND_PERCENTILE',
    'RECORDER_SILENCE_DURATION',
    'RECORDER_MAX_SECONDS'
  ],

  // Advanced - power user settings
  advancedKeys: [
    'STT_HOST',
    'STT_SERVER_PORT',
    'FASTER_WHISPER_DEVICE',
    'FASTER_WHISPER_CUDA_DEVICE',
    'FASTER_WHISPER_COMPUTE_TYPE',
    'FASTER_WHISPER_BEAM_SIZE',
    'FASTER_WHISPER_NUM_WORKERS',
    'FASTER_WHISPER_VAD_FILTER',
    'RECORDER_SILENCE_THRESHOLD',
    'RECORDER_SPEECH_DURATION',
    'RECORDER_BEEP_WAIT_TIME'
  ],

  render(modal) {
    return `
      <div class="stt-settings">
        <div class="settings-list">
          ${modal.renderCategorySettings(this.essentialKeys)}
        </div>
        
        ${modal.renderAdvancedAccordion('stt-advanced', this.advancedKeys)}
      </div>
    `;
  },

  attachListeners(modal, container) {
    modal.attachAccordionListeners(container);
  }
};