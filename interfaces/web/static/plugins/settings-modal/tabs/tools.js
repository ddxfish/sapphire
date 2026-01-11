// tabs/tools.js - Function calling and tool settings (simplified)
// TOOL_HISTORY_MAX_ENTRIES removed (debug feature, default 0)
// DELETE_EARLY_THINK_PROSE will be removed in Phase 4

export default {
  id: 'tools',
  name: 'Tools',
  icon: '🔧',
  description: 'Function calling and tool settings',
  
  // All keys this tab manages
  keys: [
    'FUNCTIONS_ENABLED',
    'MAX_TOOL_ITERATIONS',
    'MAX_PARALLEL_TOOLS',
    'DELETE_EARLY_THINK_PROSE',
    'DEBUG_TOOL_CALLING'
  ],

  // Essential - what users need
  essentialKeys: [
    'FUNCTIONS_ENABLED',
    'MAX_TOOL_ITERATIONS',
    'MAX_PARALLEL_TOOLS'
  ],

  // Advanced - debug/experimental
  advancedKeys: [
    'DELETE_EARLY_THINK_PROSE',
    'DEBUG_TOOL_CALLING'
  ],

  render(modal) {
    return `
      <div class="tools-settings">
        <div class="settings-list">
          ${modal.renderCategorySettings(this.essentialKeys)}
        </div>
        
        ${modal.renderAdvancedAccordion('tools-advanced', this.advancedKeys)}
      </div>
    `;
  },

  attachListeners(modal, container) {
    modal.attachAccordionListeners(container);
  }
};