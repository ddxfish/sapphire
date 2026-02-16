// settings-categories.js - Tab registry for settings modal
// Tabs are lazy-loaded on first access to reduce initial page load

// Tab metadata - modules loaded on demand
const TAB_METADATA = [
  { id: 'identity', name: 'Identity', icon: '👤', description: 'User identity settings', keys: ['DEFAULT_USERNAME', 'AVATARS_IN_CHAT'] },
  { id: 'appearance', name: 'Appearance', icon: '🎨', description: 'Theme and visual settings', keys: [] },
  { id: 'audio', name: 'Audio', icon: '🔊', description: 'Audio device settings', keys: ['AUDIO_OUTPUT_DEVICE', 'AUDIO_INPUT_DEVICE', 'STEREO_DOWNMIX', 'SOUND_EFFECTS', 'SEND_SOUND_PATH', 'RECEIVE_SOUND_PATH', 'SEND_SOUND_VOLUME', 'RECEIVE_SOUND_VOLUME'] },
  { id: 'tts', name: 'TTS', icon: '🗣️', description: 'Text-to-speech settings', keys: ['TTS_VOICE', 'TTS_SPEED', 'TTS_PITCH', 'KOKORO_HOST', 'KOKORO_PORT', 'KOKORO_GPU', 'KOKORO_TOP_P', 'KOKORO_TEMPERATURE'] },
  { id: 'stt', name: 'STT', icon: '🎤', description: 'Speech-to-text settings', keys: ['WHISPER_MODEL', 'STT_SAMPLE_RATE', 'STT_BLOCK_SIZE', 'STT_SILENCE_THRESHOLD', 'STT_MIN_DURATION', 'STT_MAX_DURATION', 'STT_SILENCE_DURATION', 'VAD_AGGRESSIVENESS', 'VAD_SAMPLE_RATE', 'VAD_FRAME_DURATION', 'VAD_PADDING_DURATION'] },
  { id: 'llm', name: 'LLM', icon: '🧠', description: 'Language model configuration', keys: ['LLM_PRIMARY', 'LLM_FALLBACK', 'CLAUDE_MODEL', 'OPENAI_MODEL', 'LMSTUDIO_HOST', 'LMSTUDIO_PORT', 'LMSTUDIO_MODEL', 'MAX_TOKENS', 'TEMPERATURE', 'CONTEXT_STRATEGY', 'CONTEXT_MAX_TOKENS', 'CONTEXT_RESERVE_TOKENS'] },
  { id: 'tools', name: 'Tools', icon: '🔧', description: 'Tool and function settings', keys: ['TOOL_CALL_LIMIT'] },
  { id: 'network', name: 'Network', icon: '🌐', description: 'SOCKS proxy and privacy network settings', keys: ['SOCKS_ENABLED', 'SOCKS_HOST', 'SOCKS_PORT', 'SOCKS_TIMEOUT', 'PRIVACY_NETWORK_WHITELIST'] },
  { id: 'wakeword', name: 'Wake Word', icon: '👂', description: 'Wake word detection settings', keys: ['WAKEWORD_ENABLED', 'WAKEWORD_MODEL', 'WAKEWORD_THRESHOLD', 'WAKEWORD_FRAMEWORK', 'WAKEWORD_DEBOUNCE_TIME'] },
  { id: 'system', name: 'System', icon: '⚙️', description: 'System and maintenance', keys: [] }
];

// Cache for loaded tab modules
const loadedTabs = new Map();

// Build TABS array with lazy loading
export const TABS = TAB_METADATA.map(meta => ({
  ...meta,
  _loaded: false,
  _module: null,

  // Lazy render - loads module on first call
  render(modal) {
    const cached = loadedTabs.get(meta.id);
    if (cached) {
      return cached.render(modal);
    }
    // Return loading placeholder - will be replaced after load
    return `<div class="tab-loading" data-tab-id="${meta.id}">Loading...</div>`;
  },

  // Lazy attachListeners - loads module on first call
  attachListeners(modal, contentEl) {
    const cached = loadedTabs.get(meta.id);
    if (cached && cached.attachListeners) {
      cached.attachListeners(modal, contentEl);
    }
  }
}));

// Load a tab module dynamically
export async function loadTab(tabId) {
  if (loadedTabs.has(tabId)) {
    return loadedTabs.get(tabId);
  }

  try {
    const module = await import(`./tabs/${tabId}.js`);
    const tab = module.default;
    loadedTabs.set(tabId, tab);
    return tab;
  } catch (e) {
    console.error(`Failed to load tab ${tabId}:`, e);
    return null;
  }
}

// Preload specific tabs (call during init for commonly used tabs)
export async function preloadTabs(tabIds) {
  await Promise.all(tabIds.map(id => loadTab(id)));
}

// Check if a tab is loaded
export function isTabLoaded(tabId) {
  return loadedTabs.has(tabId);
}

// Get loaded tab module
export function getLoadedTab(tabId) {
  return loadedTabs.get(tabId);
}

// Build CATEGORIES object for backward compatibility
export const CATEGORIES = Object.fromEntries(
  TABS.map(tab => [tab.id, tab])
);

// Get category/tab for a specific settings key
export function getCategoryForKey(key) {
  for (const tab of TAB_METADATA) {
    if (tab.keys && tab.keys.includes(key)) {
      return tab.id;
    }
  }
  return 'system';
}

// Get all keys for a category
export function getKeysForCategory(categoryId) {
  const meta = TAB_METADATA.find(t => t.id === categoryId);
  return meta?.keys || [];
}

// Get tab by id
export function getTab(id) {
  return CATEGORIES[id];
}
