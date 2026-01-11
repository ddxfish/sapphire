// tabs/tools.js - Function calling and tool settings (simplified)

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
    'DEBUG_TOOL_CALLING'
  ],

  // Essential - what users need
  essentialKeys: [
    'FUNCTIONS_ENABLED',
    'MAX_TOOL_ITERATIONS',
    'MAX_PARALLEL_TOOLS'
  ],

  // Advanced - debug only
  advancedKeys: [
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