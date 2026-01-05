// settings-categories.js - Tab registry for settings modal
// Import all tab modules and export them as TABS array

import identityTab from './tabs/identity.js';
import appearanceTab from './tabs/appearance.js';
import ttsTab from './tabs/tts.js';
import sttTab from './tabs/stt.js';
import llmTab from './tabs/llm.js';
import toolsTab from './tabs/tools.js';
import networkTab from './tabs/network.js';
import wakewordTab from './tabs/wakeword.js';
import systemTab from './tabs/system.js';

// Ordered array of all tabs
export const TABS = [
  identityTab,
  appearanceTab,
  ttsTab,
  sttTab,
  llmTab,
  toolsTab,
  networkTab,
  wakewordTab,
  systemTab
];

// Build CATEGORIES object for backward compatibility
export const CATEGORIES = Object.fromEntries(
  TABS.map(tab => [tab.id, tab])
);

// Get category/tab for a specific settings key
export function getCategoryForKey(key) {
  for (const tab of TABS) {
    if (tab.keys && tab.keys.includes(key)) {
      return tab.id;
    }
  }
  return 'system';
}

// Get all keys for a category
export function getKeysForCategory(categoryId) {
  const tab = CATEGORIES[categoryId];
  return tab?.keys || [];
}

// Get tab by id
export function getTab(id) {
  return CATEGORIES[id];
}