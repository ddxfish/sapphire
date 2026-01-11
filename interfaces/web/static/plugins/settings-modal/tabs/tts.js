// tabs/tts.js - Text-to-speech settings

export default {
  id: 'tts',
  name: 'TTS',
  icon: '🔊',
  description: 'Text-to-speech settings',
  
  keys: [
    'TTS_ENABLED',
    'TTS_SERVER_HOST',
    'TTS_SERVER_PORT',
    'TTS_PRIMARY_SERVER',
    'TTS_FALLBACK_SERVER',
    'TTS_FALLBACK_TIMEOUT'
  ],

  essentialKeys: [
    'TTS_ENABLED'
  ],

  advancedKeys: [
    'TTS_SERVER_HOST',
    'TTS_SERVER_PORT',
    'TTS_PRIMARY_SERVER',
    'TTS_FALLBACK_SERVER',
    'TTS_FALLBACK_TIMEOUT'
  ],

  render(modal) {
    return `
      <div class="tts-settings">
        <div class="settings-list">
          ${modal.renderCategorySettings(this.essentialKeys)}
        </div>
        
        ${modal.renderAdvancedAccordion('tts-advanced', this.advancedKeys)}
      </div>
    `;
  },

  attachListeners(modal, container) {
    modal.attachAccordionListeners(container);
  }
};