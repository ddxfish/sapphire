// tabs/audio.js - Audio device configuration
// Unified audio settings for input/output device selection and testing

export default {
  id: 'audio',
  name: 'Audio',
  icon: '🎧',
  description: 'Audio device selection and testing',
  keys: [
    'AUDIO_INPUT_DEVICE',
    'AUDIO_OUTPUT_DEVICE',
    'AUDIO_SAMPLE_RATES',
    'AUDIO_BLOCKSIZE_FALLBACKS',
    'AUDIO_PREFERRED_DEVICES_LINUX',
    'AUDIO_PREFERRED_DEVICES_WINDOWS'
  ],

  render(modal) {
    return `
      <div class="audio-settings">
        <div class="audio-devices-section">
          <h4>Input Device (Microphone)</h4>
          <p class="section-desc">Select microphone for voice input. Use "Auto-detect" for automatic selection.</p>
          <div class="device-row">
            <select id="audio-input-select" class="device-select">
              <option value="auto">Auto-detect (recommended)</option>
            </select>
            <button class="btn btn-sm btn-test" id="test-input-btn">
              <span class="btn-icon">🎤</span> Test
            </button>
          </div>
          <div class="test-result" id="input-test-result"></div>
          <div class="level-meter-container" id="input-level-container" style="display:none;">
            <div class="level-meter">
              <div class="level-bar" id="input-level-bar"></div>
            </div>
            <span class="level-value" id="input-level-value">0%</span>
          </div>
        </div>

        <div class="audio-devices-section">
          <h4>Output Device (Speakers)</h4>
          <p class="section-desc">Select speakers/headphones for TTS playback. Use "System default" for automatic selection.</p>
          <div class="device-row">
            <select id="audio-output-select" class="device-select">
              <option value="auto">System default</option>
            </select>
            <button class="btn btn-sm btn-test" id="test-output-btn">
              <span class="btn-icon">🔊</span> Test
            </button>
          </div>
          <div class="test-result" id="output-test-result"></div>
        </div>

        <div class="audio-advanced-section">
          <div class="accordion-header collapsed" id="audio-advanced-toggle">
            <span class="accordion-toggle collapsed"></span>
            <h4>Advanced Settings</h4>
          </div>
          <div class="accordion-content collapsed" id="audio-advanced-content">
            <div class="settings-list">
              ${modal.renderCategorySettings([
                'AUDIO_SAMPLE_RATES',
                'AUDIO_BLOCKSIZE_FALLBACKS',
                'AUDIO_PREFERRED_DEVICES_LINUX',
                'AUDIO_PREFERRED_DEVICES_WINDOWS'
              ])}
            </div>
          </div>
        </div>
      </div>
    `;
  },

  attachListeners(modal, container) {
    // Load devices on tab activation
    this.loadDevices(container, modal);
    
    // Input device change
    const inputSelect = container.querySelector('#audio-input-select');
    if (inputSelect) {
      inputSelect.addEventListener('change', async (e) => {
        const value = e.target.value === 'auto' ? null : parseInt(e.target.value);
        await this.updateSetting(modal, 'AUDIO_INPUT_DEVICE', value);
      });
    }
    
    // Output device change
    const outputSelect = container.querySelector('#audio-output-select');
    if (outputSelect) {
      outputSelect.addEventListener('change', async (e) => {
        const value = e.target.value === 'auto' ? null : parseInt(e.target.value);
        await this.updateSetting(modal, 'AUDIO_OUTPUT_DEVICE', value);
      });
    }
    
    // Test input button
    const testInputBtn = container.querySelector('#test-input-btn');
    if (testInputBtn) {
      testInputBtn.addEventListener('click', () => this.testInput(container));
    }
    
    // Test output button
    const testOutputBtn = container.querySelector('#test-output-btn');
    if (testOutputBtn) {
      testOutputBtn.addEventListener('click', () => this.testOutput(container));
    }
    
    // Advanced section toggle
    const advToggle = container.querySelector('#audio-advanced-toggle');
    const advContent = container.querySelector('#audio-advanced-content');
    if (advToggle && advContent) {
      advToggle.addEventListener('click', () => {
        const toggle = advToggle.querySelector('.accordion-toggle');
        advToggle.classList.toggle('collapsed');
        advContent.classList.toggle('collapsed');
        if (toggle) toggle.classList.toggle('collapsed');
      });
    }
  },

  async loadDevices(container, modal) {
    try {
      const res = await fetch('/api/audio/devices');
      if (!res.ok) throw new Error('Failed to fetch devices');
      
      const data = await res.json();
      
      // Populate input devices
      const inputSelect = container.querySelector('#audio-input-select');
      if (inputSelect && data.input) {
        // Keep auto option
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
        
        // Select auto if configured_input is null
        if (data.configured_input === null) {
          inputSelect.value = 'auto';
        }
      }
      
      // Populate output devices
      const outputSelect = container.querySelector('#audio-output-select');
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
      
    } catch (e) {
      console.error('Failed to load audio devices:', e);
      const inputResult = container.querySelector('#input-test-result');
      if (inputResult) {
        inputResult.textContent = `⚠ Could not load devices: ${e.message}`;
        inputResult.className = 'test-result error';
      }
    }
  },

  async updateSetting(modal, key, value) {
    try {
      const res = await fetch('/api/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value })
      });
      
      if (!res.ok) throw new Error('Failed to update setting');
      
      // Update local cache
      modal.settings[key] = value;
      modal.pendingChanges[key] = value;
      
    } catch (e) {
      console.error(`Failed to update ${key}:`, e);
    }
  },

  async testInput(container) {
    const btn = container.querySelector('#test-input-btn');
    const result = container.querySelector('#input-test-result');
    const levelContainer = container.querySelector('#input-level-container');
    const levelBar = container.querySelector('#input-level-bar');
    const levelValue = container.querySelector('#input-level-value');
    const select = container.querySelector('#audio-input-select');
    
    const deviceIndex = select?.value === 'auto' ? null : select?.value;
    
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> Recording...';
    result.textContent = '';
    result.className = 'test-result';
    levelContainer.style.display = 'flex';
    levelBar.style.width = '0%';
    levelValue.textContent = '0%';
    
    try {
      const res = await fetch('/api/audio/test-input', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_index: deviceIndex, duration: 1.5 })
      });
      
      const data = await res.json();
      
      if (data.success) {
        const peakPct = Math.round(data.peak_level * 100);
        levelBar.style.width = `${Math.min(peakPct, 100)}%`;
        levelValue.textContent = `${peakPct}%`;
        
        // Color code the level
        if (peakPct < 5) {
          result.textContent = `⚠ Very low level (${peakPct}%) - check microphone`;
          result.className = 'test-result warning';
          levelBar.style.background = 'var(--accent-yellow, #f0ad4e)';
        } else if (peakPct > 90) {
          result.textContent = `⚠ Very high level (${peakPct}%) - may clip`;
          result.className = 'test-result warning';
          levelBar.style.background = 'var(--accent-red, #d9534f)';
        } else {
          result.textContent = `✓ Audio detected from ${data.device_name}`;
          result.className = 'test-result success';
          levelBar.style.background = 'var(--accent-green, #5cb85c)';
        }
      } else {
        result.textContent = `✗ ${data.error}`;
        result.className = 'test-result error';
        levelContainer.style.display = 'none';
      }
      
    } catch (e) {
      result.textContent = `✗ Test failed: ${e.message}`;
      result.className = 'test-result error';
      levelContainer.style.display = 'none';
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<span class="btn-icon">🎤</span> Test';
    }
  },

  async testOutput(container) {
    const btn = container.querySelector('#test-output-btn');
    const result = container.querySelector('#output-test-result');
    const select = container.querySelector('#audio-output-select');
    
    const deviceIndex = select?.value === 'auto' ? null : select?.value;
    
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-icon">⏳</span> Playing...';
    result.textContent = '';
    result.className = 'test-result';
    
    try {
      const res = await fetch('/api/audio/test-output', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_index: deviceIndex, duration: 0.5, frequency: 440 })
      });
      
      const data = await res.json();
      
      if (data.success) {
        result.textContent = '✓ Test tone played successfully';
        result.className = 'test-result success';
      } else {
        result.textContent = `✗ ${data.error}`;
        result.className = 'test-result error';
      }
      
    } catch (e) {
      result.textContent = `✗ Test failed: ${e.message}`;
      result.className = 'test-result error';
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<span class="btn-icon">🔊</span> Test';
    }
  }
};