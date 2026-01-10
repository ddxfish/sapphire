// shared/audio-devices.js - Reusable audio device UI components
// Used by: settings-modal/tabs/audio.js, setup-wizard

import { fetchWithTimeout } from './fetch.js';

// ============================================================================
// API Functions
// ============================================================================

/**
 * Fetch available audio devices from backend.
 */
export async function fetchAudioDevices() {
  return await fetchWithTimeout('/api/audio/devices');
}

/**
 * Test input device (microphone).
 * @param {number|null} deviceIndex - Device index or null for auto
 * @param {number} duration - Recording duration in seconds
 */
export async function testInputDevice(deviceIndex = null, duration = 1.5) {
  const res = await fetch('/api/audio/test-input', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ device_index: deviceIndex, duration })
  });
  return await res.json();
}

/**
 * Test output device (speakers).
 * @param {number|null} deviceIndex - Device index or null for auto
 * @param {number} duration - Tone duration in seconds
 * @param {number} frequency - Tone frequency in Hz
 */
export async function testOutputDevice(deviceIndex = null, duration = 0.5, frequency = 440) {
  const res = await fetch('/api/audio/test-output', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ device_index: deviceIndex, duration, frequency })
  });
  return await res.json();
}

/**
 * Update audio device setting.
 * @param {string} key - Setting key (AUDIO_INPUT_DEVICE or AUDIO_OUTPUT_DEVICE)
 * @param {number|null} value - Device index or null for auto
 */
export async function updateAudioSetting(key, value) {
  const res = await fetch(`/api/settings/${key}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value })
  });
  if (!res.ok) {
    throw new Error('Failed to update audio setting');
  }
  return await res.json();
}

// ============================================================================
// Render Functions
// ============================================================================

/**
 * Render input device selector HTML.
 * @param {string} selectId - ID for the select element
 */
export function renderInputDeviceSelector(selectId = 'audio-input-select') {
  return `
    <div class="audio-device-row">
      <select id="${selectId}" class="device-select">
        <option value="auto">Auto-detect (recommended)</option>
      </select>
      <button class="btn btn-sm btn-test" data-test="input">
        <span class="btn-icon">🎤</span> Test
      </button>
    </div>
    <div class="test-result" data-result="input"></div>
    <div class="level-meter-container" data-meter="input" style="display:none;">
      <div class="level-meter">
        <div class="level-bar" data-bar="input"></div>
      </div>
      <span class="level-value" data-value="input">0%</span>
    </div>
  `;
}

/**
 * Render output device selector HTML.
 * @param {string} selectId - ID for the select element
 */
export function renderOutputDeviceSelector(selectId = 'audio-output-select') {
  return `
    <div class="audio-device-row">
      <select id="${selectId}" class="device-select">
        <option value="auto">System default</option>
      </select>
      <button class="btn btn-sm btn-test" data-test="output">
        <span class="btn-icon">🔊</span> Test
      </button>
    </div>
    <div class="test-result" data-result="output"></div>
  `;
}

/**
 * Render complete audio devices section.
 */
export function renderAudioDevicesSection() {
  return `
    <div class="audio-devices-section">
      <div class="audio-input-section">
        <h4>Input Device (Microphone)</h4>
        <p class="section-desc">Select microphone for voice input. Use "Auto-detect" for automatic selection.</p>
        ${renderInputDeviceSelector()}
      </div>

      <div class="audio-output-section">
        <h4>Output Device (Speakers)</h4>
        <p class="section-desc">Select speakers/headphones for TTS playback. Use "System default" for automatic selection.</p>
        ${renderOutputDeviceSelector()}
      </div>
    </div>
  `;
}

// ============================================================================
// UI Population
// ============================================================================

/**
 * Populate device select elements with available devices.
 * @param {HTMLElement} container - Container with select elements
 * @param {string} inputSelectId - ID of input device select
 * @param {string} outputSelectId - ID of output device select
 */
export async function populateDeviceSelects(container, inputSelectId = 'audio-input-select', outputSelectId = 'audio-output-select') {
  try {
    const data = await fetchAudioDevices();

    // Populate input devices
    const inputSelect = container.querySelector(`#${inputSelectId}`);
    if (inputSelect && data.input) {
      inputSelect.innerHTML = '<option value="auto">Auto-detect (recommended)</option>';

      for (const dev of data.input) {
        const opt = document.createElement('option');
        opt.value = dev.index;
        opt.textContent = `[${dev.index}] ${dev.name}${dev.is_default ? ' (default)' : ''}`;
        if (data.configured_input === dev.index) {
          opt.selected = true;
        }
        inputSelect.appendChild(opt);
      }

      if (data.configured_input === null) {
        inputSelect.value = 'auto';
      }
    }

    // Populate output devices
    const outputSelect = container.querySelector(`#${outputSelectId}`);
    if (outputSelect && data.output) {
      outputSelect.innerHTML = '<option value="auto">System default</option>';

      for (const dev of data.output) {
        const opt = document.createElement('option');
        opt.value = dev.index;
        opt.textContent = `[${dev.index}] ${dev.name}${dev.is_default ? ' (default)' : ''}`;
        if (data.configured_output === dev.index) {
          opt.selected = true;
        }
        outputSelect.appendChild(opt);
      }

      if (data.configured_output === null) {
        outputSelect.value = 'auto';
      }
    }

    return data;
  } catch (e) {
    console.error('Failed to load audio devices:', e);
    throw e;
  }
}

// ============================================================================
// Test UI Functions
// ============================================================================

/**
 * Run input device test with UI feedback.
 * @param {HTMLElement} container - Container with test elements
 * @param {string} selectId - ID of device select
 */
export async function runInputTest(container, selectId = 'audio-input-select') {
  const btn = container.querySelector('[data-test="input"]');
  const result = container.querySelector('[data-result="input"]');
  const levelContainer = container.querySelector('[data-meter="input"]');
  const levelBar = container.querySelector('[data-bar="input"]');
  const levelValue = container.querySelector('[data-value="input"]');
  const select = container.querySelector(`#${selectId}`);

  const deviceIndex = select?.value === 'auto' ? null : parseInt(select?.value);

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> Recording...';
  }
  if (result) {
    result.textContent = '';
    result.className = 'test-result';
  }
  if (levelContainer) {
    levelContainer.style.display = 'flex';
  }
  if (levelBar) {
    levelBar.style.width = '0%';
  }
  if (levelValue) {
    levelValue.textContent = '0%';
  }

  try {
    const data = await testInputDevice(deviceIndex);

    if (data.success) {
      const peakPct = Math.round(data.peak_level * 100);
      
      if (levelBar) {
        levelBar.style.width = `${Math.min(peakPct, 100)}%`;
      }
      if (levelValue) {
        levelValue.textContent = `${peakPct}%`;
      }

      // Color code the level
      if (peakPct < 5) {
        if (result) {
          result.textContent = `⚠ Very low level (${peakPct}%) - check microphone`;
          result.className = 'test-result warning';
        }
        if (levelBar) levelBar.style.background = 'var(--accent-yellow, #f0ad4e)';
      } else if (peakPct > 90) {
        if (result) {
          result.textContent = `⚠ Very high level (${peakPct}%) - may clip`;
          result.className = 'test-result warning';
        }
        if (levelBar) levelBar.style.background = 'var(--accent-red, #d9534f)';
      } else {
        if (result) {
          result.textContent = `✓ Audio detected from ${data.device_name}`;
          result.className = 'test-result success';
        }
        if (levelBar) levelBar.style.background = 'var(--accent-green, #5cb85c)';
      }
    } else {
      if (result) {
        result.textContent = `✗ ${data.error}`;
        result.className = 'test-result error';
      }
      if (levelContainer) levelContainer.style.display = 'none';
    }
  } catch (e) {
    if (result) {
      result.textContent = `✗ Test failed: ${e.message}`;
      result.className = 'test-result error';
    }
    if (levelContainer) levelContainer.style.display = 'none';
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span class="btn-icon">🎤</span> Test';
    }
  }
}

/**
 * Run output device test with UI feedback.
 * @param {HTMLElement} container - Container with test elements
 * @param {string} selectId - ID of device select
 */
export async function runOutputTest(container, selectId = 'audio-output-select') {
  const btn = container.querySelector('[data-test="output"]');
  const result = container.querySelector('[data-result="output"]');
  const select = container.querySelector(`#${selectId}`);

  const deviceIndex = select?.value === 'auto' ? null : parseInt(select?.value);

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> Playing...';
  }
  if (result) {
    result.textContent = '';
    result.className = 'test-result';
  }

  try {
    const data = await testOutputDevice(deviceIndex);

    if (data.success) {
      if (result) {
        result.textContent = '✓ Test tone played successfully';
        result.className = 'test-result success';
      }
    } else {
      if (result) {
        result.textContent = `✗ ${data.error}`;
        result.className = 'test-result error';
      }
    }
  } catch (e) {
    if (result) {
      result.textContent = `✗ Test failed: ${e.message}`;
      result.className = 'test-result error';
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span class="btn-icon">🔊</span> Test';
    }
  }
}

// ============================================================================
// Listener Setup
// ============================================================================

/**
 * Attach all standard audio device listeners to a container.
 * @param {HTMLElement} container - Container with audio UI elements
 * @param {object} options - Configuration options
 * @param {string} options.inputSelectId - Input select ID
 * @param {string} options.outputSelectId - Output select ID
 * @param {function} options.onInputChange - Callback when input device changes
 * @param {function} options.onOutputChange - Callback when output device changes
 */
export function attachAudioDeviceListeners(container, options = {}) {
  const {
    inputSelectId = 'audio-input-select',
    outputSelectId = 'audio-output-select',
    onInputChange = null,
    onOutputChange = null
  } = options;

  // Input device change
  const inputSelect = container.querySelector(`#${inputSelectId}`);
  if (inputSelect) {
    inputSelect.addEventListener('change', async (e) => {
      const value = e.target.value === 'auto' ? null : parseInt(e.target.value);
      await updateAudioSetting('AUDIO_INPUT_DEVICE', value);
      if (onInputChange) onInputChange(value);
    });
  }

  // Output device change
  const outputSelect = container.querySelector(`#${outputSelectId}`);
  if (outputSelect) {
    outputSelect.addEventListener('change', async (e) => {
      const value = e.target.value === 'auto' ? null : parseInt(e.target.value);
      await updateAudioSetting('AUDIO_OUTPUT_DEVICE', value);
      if (onOutputChange) onOutputChange(value);
    });
  }

  // Test input button
  const testInputBtn = container.querySelector('[data-test="input"]');
  if (testInputBtn) {
    testInputBtn.addEventListener('click', () => runInputTest(container, inputSelectId));
  }

  // Test output button
  const testOutputBtn = container.querySelector('[data-test="output"]');
  if (testOutputBtn) {
    testOutputBtn.addEventListener('click', () => runOutputTest(container, outputSelectId));
  }
}

/**
 * Initialize audio devices UI - load devices and attach listeners.
 * @param {HTMLElement} container - Container with audio UI elements
 * @param {object} options - Options passed to attachAudioDeviceListeners
 */
export async function initAudioDevicesUI(container, options = {}) {
  await populateDeviceSelects(
    container,
    options.inputSelectId || 'audio-input-select',
    options.outputSelectId || 'audio-output-select'
  );
  attachAudioDeviceListeners(container, options);
}