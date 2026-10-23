// shared/plugin-registry.js - Registry for plugin settings tabs
// Plugins register their settings UI here; Settings view reads them via getRegisteredTabs()

const settingsTabs = new Map();

/**
 * Register a plugin's settings tab
 * @param {Object} config
 * @param {string} config.id - Unique plugin identifier
 * @param {string} config.name - Display name for tab
 * @param {string} config.icon - Emoji/icon for tab
 * @param {string} [config.helpText] - Optional help text shown at top of tab
 * @param {Function} config.render - (container) => void - Render settings UI
 * @param {Function} config.load - async () => settings - Load current settings
 * @param {Function} config.save - async (settings) => void - Save settings
 * @param {Function} [config.getSettings] - () => settings - Get current form values
 * @param {Function} [config.reset] - async () => void - Delete stored settings so
 *   manifest defaults take over (shows a Reset to Defaults button in the header).
 *   Omit on tabs with side-channel stored state (e.g. MCP's servers) — the reset
 *   endpoint removes the whole stored file.
 */
export function registerPluginSettings(config) {
  if (!config.id || !config.name || !config.render) {
    console.warn('[PluginRegistry] Invalid config, needs id, name, render:', config);
    return false;
  }

  settingsTabs.set(config.id, {
    id: config.id,
    name: config.name,
    icon: config.icon || '⚙️',
    helpText: config.helpText || null,
    render: config.render,
    load: config.load || (() => Promise.resolve({})),
    save: config.save || (() => Promise.resolve()),
    getSettings: config.getSettings || null,
    reset: config.reset || null
  });

  console.log(`[PluginRegistry] Registered settings tab: ${config.name}`);
  return true;
}

/**
 * Unregister a plugin's settings tab
 */
export function unregisterPluginSettings(id) {
  return settingsTabs.delete(id);
}

/**
 * Get all registered settings tabs
 * @returns {Array} Array of tab configs
 */
export function getRegisteredTabs() {
  return Array.from(settingsTabs.values());
}

/**
 * Get a specific tab config
 */
export function getTab(id) {
  return settingsTabs.get(id);
}

/**
 * Check if any tabs are registered
 */
export function hasRegisteredTabs() {
  return settingsTabs.size > 0;
}

// Export for debugging
window._pluginSettingsRegistry = settingsTabs;
