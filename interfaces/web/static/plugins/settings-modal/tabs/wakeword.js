// tabs/wakeword.js - Wake word detection settings (simplified)
// Essential settings visible by default, advanced in accordion

export default {
  id: 'wakeword',
  name: 'Wakeword',
  icon: '🎵',
  description: 'Wake word detection settings',
  
  // All keys this tab manages
  keys: [
    'WAKE_WORD_ENABLED',
    'WAKEWORD_MODEL',
    'WAKEWORD_THRESHOLD',
    'WAKEWORD_FRAMEWORK',
    'CHUNK_SIZE',
    'BUFFER_DURATION',
    'WAKE_TONE_DURATION',
    'WAKE_TONE_FREQUENCY'
  ],

  // Essential - what users actually need to configure
  essentialKeys: [
    'WAKE_WORD_ENABLED',
    'WAKEWORD_MODEL',
    'WAKEWORD_THRESHOLD'
  ],

  // Advanced - tweaking for edge cases
  advancedKeys: [
    'WAKEWORD_FRAMEWORK',
    'CHUNK_SIZE',
    'BUFFER_DURATION',
    'WAKE_TONE_DURATION',
    'WAKE_TONE_FREQUENCY'
  ],

  render(modal) {
    return `
      <div class="wakeword-settings">
        <div class="settings-list">
          ${modal.renderCategorySettings(this.essentialKeys)}
        </div>
        
        ${modal.renderAdvancedAccordion('wakeword-advanced', this.advancedKeys)}
      </div>
    `;
  },

  attachListeners(modal, container) {
    modal.attachAccordionListeners(container);
  }
};