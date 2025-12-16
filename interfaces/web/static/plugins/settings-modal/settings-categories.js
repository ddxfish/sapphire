// categories.js - Category mapping for settings organization
export const CATEGORIES = {
  
  identity: {
    name: 'Identity',
    icon: '👤',
    description: 'User and AI identity settings',
    keys: ['DEFAULT_USERNAME', 'DEFAULT_AI_NAME']
  },
  
  tts: {
    name: 'TTS',
    icon: '🔊',
    description: 'Text-to-speech settings',
    keys: [
      'TTS_ENABLED',
      'TTS_VOICE_NAME',
      'TTS_SPEED',
      'TTS_PITCH_SHIFT',
      'TTS_PRIMARY_SERVER',
      'TTS_FALLBACK_SERVER',
      'TTS_FALLBACK_TIMEOUT',
      'TTS_AUDIO_PLAYER'
    ]
  },
  
  stt: {
    name: 'STT',
    icon: '🎤',
    description: 'Speech-to-text settings',
    keys: [
      'STT_ENABLED',
      'STT_ENGINE',
      'STT_MODEL_SIZE',
      'STT_LANGUAGE',
      'STT_HOST',
      'STT_SERVER_PORT',
      'TRANSCRIBE_SAMPLE_RATE',
      'TRANSCRIBE_CHANNELS',
      'FASTER_WHISPER_CUDA_DEVICE',
      'FASTER_WHISPER_DEVICE',
      'FASTER_WHISPER_COMPUTE_TYPE',
      'FASTER_WHISPER_BEAM_SIZE',
      'FASTER_WHISPER_NUM_WORKERS',
      'FASTER_WHISPER_VAD_FILTER',
      'FASTER_WHISPER_VAD_PARAMETERS'
    ]
  },
  
  llm: {
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
    ]
  },
  
  tools: {
    name: 'Tools',
    icon: '🔧',
    description: 'Function calling and tool settings',
    keys: [
      'FUNCTIONS_ENABLED',
      'TOOL_HISTORY_MAX_ENTRIES',
      'MAX_TOOL_ITERATIONS',
      'MAX_PARALLEL_TOOLS',
      'DELETE_EARLY_THINK_PROSE',
      'DEBUG_TOOL_CALLING',
      'ABILITIES'
    ]
  },
  
  network: {
    name: 'Network',
    icon: '🌐',
    description: 'Network and proxy settings',
    keys: [
      'SOCKS_ENABLED',
      'SOCKS_HOST',
      'SOCKS_PORT'
    ]
  },
  
  wakeword: {
    name: 'Wakeword',
    icon: '🎵',
    description: 'Wake word detection and audio recording',
    keys: [
      'WAKE_WORD_ENABLED',
      'WAKEWORD_MODEL',
      'WAKEWORD_THRESHOLD',
      'WAKEWORD_FRAMEWORK',
      'WAKEWORD_DEVICE_INDEX',
      'CHUNK_SIZE',
      'BUFFER_DURATION',
      'FRAME_SKIP',
      'RECORDING_SAMPLE_RATE',
      'PLAYBACK_SAMPLE_RATE',
      'WAKE_TONE_DURATION',
      'WAKE_TONE_FREQUENCY',
      'CALLBACK_THREAD_POOL_SIZE',
      'RECORDER_CHUNK_SIZE',
      'RECORDER_CHANNELS',
      'RECORDER_SILENCE_THRESHOLD',
      'RECORDER_SILENCE_DURATION',
      'RECORDER_SPEECH_DURATION',
      'RECORDER_LEVEL_HISTORY_SIZE',
      'RECORDER_BACKGROUND_PERCENTILE',
      'RECORDER_NOISE_MULTIPLIER',
      'RECORDER_MAX_SECONDS',
      'RECORDER_BEEP_WAIT_TIME',
      'RECORDER_SAMPLE_RATES',
      'RECORDER_PREFERRED_DEVICES'
    ]
  },
  
  system: {
    name: 'System',
    icon: '⚡',
    description: 'System and advanced settings',
    keys: [
      'MODULES_ENABLED',
      'PLUGINS_ENABLED'
    ]
  },
  appearance: {
    name: 'Visual',
    icon: '🎨',
    description: 'Theme and visual settings',
    keys: []  // Theme is localStorage, not backend config
  }
};

// Get category for a specific key
export function getCategoryForKey(key) {
  for (const [catId, category] of Object.entries(CATEGORIES)) {
    if (category.keys.includes(key)) {
      return catId;
    }
  }
  return 'system'; // Default fallback
}

// Get all keys in a category
export function getKeysForCategory(categoryId) {
  return CATEGORIES[categoryId]?.keys || [];
}