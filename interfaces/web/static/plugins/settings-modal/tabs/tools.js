// tabs/tools.js - Function calling and tool settings

export default {
  id: 'tools',
  name: 'Tools',
  icon: '🔧',
  description: 'Function calling and tool settings',
  keys: [
    'FUNCTIONS_ENABLED',
    'TOOL_HISTORY_MAX_ENTRIES',
    'MAX_TOOL_ITERATIONS',
    'MAX_PARALLEL_TOOLS',
    'DELETE_EARLY_THINK_PROSE',
    'DEBUG_TOOL_CALLING'
  ],

  render(modal) {
    return `
      <div class="settings-list">
        ${modal.renderCategorySettings(this.keys)}
      </div>
    `;
  }
};