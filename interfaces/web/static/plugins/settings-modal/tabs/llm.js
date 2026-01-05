// tabs/llm.js - Language model settings

export default {
  id: 'llm',
  name: 'LLM',
  icon: '🧠',
  description: 'Language model settings',
  keys: [
    'LLM_MAX_HISTORY',
    'LLM_MAX_TOKENS',
    'LLM_REQUEST_TIMEOUT',
    'LLM_PRIMARY',
    'LLM_FALLBACK',
    'GENERATION_DEFAULTS',
    'FORCE_THINKING',
    'THINKING_PREFILL'
  ],

  render(modal) {
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(this.keys)}
      </div>
    `;
  }
};