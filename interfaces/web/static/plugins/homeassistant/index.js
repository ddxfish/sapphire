// index.js - Home Assistant settings plugin
// Settings tab in Plugins modal for HA configuration

import { registerPluginSettings } from '../plugins-modal/plugin-registry.js';
import pluginsAPI from '../plugins-modal/plugins-api.js';

function csrfHeaders(extra = {}) {
  const token = document.querySelector('meta[name="csrf-token"]')?.content || '';
  return { 'X-CSRF-Token': token, ...extra };
}

let DEFAULTS = null;

async function loadDefaults() {
  if (DEFAULTS) return DEFAULTS;
  
  try {
    const res = await fetch('/api/webui/plugins/homeassistant/defaults');
    if (res.ok) {
      DEFAULTS = await res.json();
      console.log('✔ Home Assistant defaults loaded');
      return DEFAULTS;
    }
  } catch (e) {
    console.warn('Failed to load HA defaults:', e);
  }
  
  DEFAULTS = {
    url: 'http://homeassistant.local:8123',
    blacklist: ['cover.*', 'lock.*']
  };
  return DEFAULTS;
}

function injectStyles() {
  if (document.getElementById('ha-styles')) return;
  
  const style = document.createElement('style');
  style.id = 'ha-styles';
  style.textContent = `
    .ha-form {
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    
    .ha-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    
    .ha-group label {
      font-size: 13px;
      font-weight: 500;
      color: var(--text);
    }
    
    .ha-group input,
    .ha-group textarea {
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg-primary);
      color: var(--text);
      font-size: 13px;
      font-family: inherit;
    }
    
    .ha-group input:focus,
    .ha-group textarea:focus {
      outline: none;
      border-color: var(--accent-blue);
    }
    
    .ha-group textarea {
      resize: vertical;
      min-height: 80px;
      font-family: monospace;
      font-size: 12px;
    }
    
    .ha-hint {
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 4px;
    }
    
    .ha-url-row {
      display: flex;
      gap: 8px;
      align-items: flex-start;
    }
    
    .ha-url-row input {
      flex: 1;
    }
    
    .ha-test-btn {
      padding: 8px 14px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg-tertiary);
      color: var(--text);
      cursor: pointer;
      font-size: 13px;
      white-space: nowrap;
      transition: all 0.15s ease;
    }
    
    .ha-test-btn:hover {
      background: var(--bg-hover);
    }
    
    .ha-test-btn:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }
    
    .ha-test-btn.success {
      background: var(--success-light, #d4edda);
      border-color: var(--success, #28a745);
      color: var(--success, #28a745);
    }
    
    .ha-test-btn.error {
      background: var(--error-light, #f8d7da);
      border-color: var(--error, #dc3545);
      color: var(--error, #dc3545);
    }
    
    .ha-section {
      border-top: 1px solid var(--border);
      padding-top: 16px;
      margin-top: 8px;
    }
    
    .ha-section-title {
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 12px;
    }
    
    .ha-token-row {
      display: flex;
      gap: 8px;
      align-items: center;
    }
    
    .ha-token-row input {
      flex: 1;
    }
    
    .ha-token-status {
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 12px;
      white-space: nowrap;
    }
    
    .ha-token-status.stored {
      background: var(--success-light, #d4edda);
      color: var(--success, #28a745);
    }
    
    .ha-token-status.missing {
      background: var(--warning-light, #fff3cd);
      color: var(--warning, #856404);
    }
    
    .ha-entities-preview {
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      margin-top: 12px;
    }
    
    .ha-entities-title {
      font-size: 12px;
      font-weight: 600;
      color: var(--text-muted);
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    
    .ha-entities-counts {
      display: flex;
      gap: 16px;
      flex-wrap: wrap;
    }
    
    .ha-count-item {
      font-size: 13px;
      color: var(--text);
    }
    
    .ha-count-item span {
      font-weight: 600;
      color: var(--accent-blue);
    }
    
    .ha-fetch-btn {
      margin-top: 8px;
      padding: 6px 12px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--bg-secondary);
      color: var(--text);
      cursor: pointer;
      font-size: 12px;
    }
    
    .ha-fetch-btn:hover {
      background: var(--bg-hover);
    }
  `;
  document.head.appendChild(style);
}

async function testConnection(container) {
  // Use document.getElementById as fallback - IDs are unique
  const btn = container.querySelector('#ha-test-btn') || document.getElementById('ha-test-btn');
  const urlInput = container.querySelector('#ha-url') || document.getElementById('ha-url');
  const tokenInput = container.querySelector('#ha-token') || document.getElementById('ha-token');
  
  if (!urlInput) {
    console.error('HA: Could not find URL input');
    if (btn) {
      btn.textContent = 'Form Error';
      btn.className = 'ha-test-btn error';
    }
    return;
  }
  
  const url = urlInput.value.trim();
  const token = tokenInput?.value?.trim() || '';  // Empty is OK - backend uses stored
  
  console.log('HA Test:', { url, hasToken: !!token, tokenLength: token.length });
  
  if (!url) {
    btn.textContent = 'No URL';
    btn.className = 'ha-test-btn error';
    return;
  }
  
  // Don't validate token here - let backend fall back to stored credentials
  
  btn.disabled = true;
  btn.textContent = 'Testing...';
  btn.className = 'ha-test-btn';
  
  try {
    const payload = { url };
    if (token) {
      payload.token = token;  // Only include if user entered one
    }
    
    const res = await fetch('/api/webui/plugins/homeassistant/test-connection', {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    
    console.log('HA Test response:', data);
    
    if (data.success) {
      btn.textContent = '✓ Connected';
      btn.className = 'ha-test-btn success';
    } else {
      btn.textContent = '✗ Failed';
      btn.className = 'ha-test-btn error';
      btn.title = data.error || 'Connection failed';
    }
  } catch (e) {
    btn.textContent = '✗ Error';
    btn.className = 'ha-test-btn error';
    btn.title = e.message;
  }
  
  btn.disabled = false;
  
  setTimeout(() => {
    btn.textContent = 'Test';
    btn.className = 'ha-test-btn';
    btn.title = '';
  }, 5000);
}

async function testNotify(container) {
  const getEl = (id) => container.querySelector(`#${id}`) || document.getElementById(id);
  
  const btn = getEl('ha-test-notify-btn');
  const urlInput = getEl('ha-url');
  const tokenInput = getEl('ha-token');
  const notifyInput = getEl('ha-notify-service');
  
  const url = urlInput?.value?.trim() || '';
  const token = tokenInput?.value?.trim() || '';
  const notifyService = notifyInput?.value?.trim() || '';
  
  if (!url) {
    btn.textContent = 'No URL';
    btn.className = 'ha-test-btn error';
    setTimeout(() => { btn.textContent = 'Test'; btn.className = 'ha-test-btn'; }, 3000);
    return;
  }
  
  if (!notifyService) {
    btn.textContent = 'No Service';
    btn.className = 'ha-test-btn error';
    setTimeout(() => { btn.textContent = 'Test'; btn.className = 'ha-test-btn'; }, 3000);
    return;
  }
  
  btn.disabled = true;
  btn.textContent = 'Sending...';
  btn.className = 'ha-test-btn';
  
  try {
    const payload = { url, notify_service: notifyService };
    if (token) payload.token = token;
    
    const res = await fetch('/api/webui/plugins/homeassistant/test-notify', {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    
    console.log('HA test-notify response:', data);
    
    if (data.success) {
      btn.textContent = '✓ Sent!';
      btn.className = 'ha-test-btn success';
    } else {
      btn.textContent = '✗ Failed';
      btn.className = 'ha-test-btn error';
      btn.title = data.error || 'Failed to send';
    }
  } catch (e) {
    console.error('HA test-notify error:', e);
    btn.textContent = '✗ Error';
    btn.className = 'ha-test-btn error';
    btn.title = e.message;
  }
  
  btn.disabled = false;
  
  setTimeout(() => {
    btn.textContent = 'Test';
    btn.className = 'ha-test-btn';
    btn.title = '';
  }, 5000);
}

async function fetchEntities(container) {
  const getEl = (id) => container.querySelector(`#${id}`) || document.getElementById(id);
  
  const btn = getEl('ha-fetch-entities');
  const preview = getEl('ha-entities-counts');
  const urlInput = getEl('ha-url');
  const tokenInput = getEl('ha-token');
  const blacklistInput = getEl('ha-blacklist');
  
  const url = urlInput?.value?.trim() || '';
  const token = tokenInput?.value?.trim() || '';
  const blacklist = (blacklistInput?.value || '').split('\n').map(s => s.trim()).filter(Boolean);
  
  if (!url) {
    preview.innerHTML = '<em>Enter URL first</em>';
    return;
  }
  
  btn.disabled = true;
  btn.textContent = 'Loading...';
  preview.innerHTML = '<em>Fetching entities...</em>';
  
  try {
    const res = await fetch('/api/webui/plugins/homeassistant/entities', {
      method: 'POST',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ url, token: token || undefined, blacklist })
    });
    const data = await res.json();
    
    if (data.success) {
      const counts = data.counts;
      const areas = data.areas || [];
      preview.innerHTML = `
        <div class="ha-count-item">Lights: <span>${counts.lights}</span></div>
        <div class="ha-count-item">Switches: <span>${counts.switches}</span></div>
        <div class="ha-count-item">Scenes: <span>${counts.scenes}</span></div>
        <div class="ha-count-item">Scripts: <span>${counts.scripts}</span></div>
        <div class="ha-count-item">Climate: <span>${counts.climate}</span></div>
        <div class="ha-count-item" style="width:100%; margin-top:8px;">Areas: <span>${areas.length ? areas.join(', ') : 'None detected'}</span></div>
      `;
    } else {
      preview.innerHTML = `<em style="color: var(--error)">${data.error || 'Failed to fetch'}</em>`;
    }
  } catch (e) {
    preview.innerHTML = `<em style="color: var(--error)">${e.message}</em>`;
  }
  
  btn.disabled = false;
  btn.textContent = 'Refresh Preview';
}

async function checkTokenStatus(container) {
  const status = container.querySelector('#ha-token-status') || document.getElementById('ha-token-status');
  if (!status) return;
  
  try {
    const res = await fetch('/api/webui/plugins/homeassistant/token');
    const data = await res.json();
    
    console.log('HA token status:', data);
    
    if (data.has_token) {
      const len = data.token_length || '?';
      if (len < 100) {
        status.textContent = `⚠ Stored (${len} chars - too short!)`;
        status.className = 'ha-token-status missing';
        status.title = 'HA tokens should be ~180+ characters';
      } else {
        status.textContent = `✓ Stored (${len} chars)`;
        status.className = 'ha-token-status stored';
      }
    } else {
      status.textContent = 'Not set';
      status.className = 'ha-token-status missing';
    }
  } catch (e) {
    console.error('HA token status error:', e);
    status.textContent = '?';
    status.className = 'ha-token-status missing';
  }
}

function renderForm(container, settings) {
  const d = DEFAULTS || { url: 'http://homeassistant.local:8123', blacklist: ['cover.*', 'lock.*'] };
  const s = { ...d, ...settings };
  
  // Ensure blacklist is array
  let blacklistArr = s.blacklist || d.blacklist;
  if (typeof blacklistArr === 'string') {
    blacklistArr = blacklistArr.split('\n').filter(Boolean);
  }
  const blacklistText = Array.isArray(blacklistArr) ? blacklistArr.join('\n') : '';
  
  container.innerHTML = `
    <div class="ha-form">
      <div class="ha-group">
        <label for="ha-url">Home Assistant URL</label>
        <div class="ha-url-row">
          <input type="text" id="ha-url" value="${s.url}" placeholder="http://homeassistant.local:8123">
          <button type="button" class="ha-test-btn" id="ha-test-btn">Test</button>
        </div>
        <div class="ha-hint">Your Home Assistant instance URL (include port if not 80/443)</div>
      </div>
      
      <div class="ha-group">
        <label for="ha-token">Long-Lived Access Token</label>
        <div class="ha-token-row">
          <input type="password" id="ha-token" placeholder="Enter new token to update...">
          <span class="ha-token-status missing" id="ha-token-status">Checking...</span>
        </div>
        <div class="ha-hint">
          Create in HA: Profile → Long-Lived Access Tokens → Create Token<br>
          Leave blank to keep existing token. Token is stored securely outside project.
        </div>
      </div>
      
      <div class="ha-group">
        <label for="ha-notify-service">Mobile App Notify Service</label>
        <div class="ha-url-row">
          <input type="text" id="ha-notify-service" value="${s.notify_service || ''}" placeholder="mobile_app_your_phone">
          <button type="button" class="ha-test-btn" id="ha-test-notify-btn">Test</button>
        </div>
        <div class="ha-hint">
          Service name for phone notifications (with or without "notify." prefix).<br>
          Find yours in HA: Developer Tools → Actions → search "notify.mobile_app"
        </div>
      </div>
      
      <div class="ha-section">
        <div class="ha-section-title">Blacklist</div>
        <div class="ha-group">
          <label for="ha-blacklist">Blocked Entities (one per line)</label>
          <textarea id="ha-blacklist" rows="6" placeholder="cover.*
lock.*
switch.computer1
area:Server Room">${blacklistText}</textarea>
          <div class="ha-hint">
            Patterns: <code>switch.computer1</code> (exact), <code>cover.*</code> (domain), <code>area:Garage</code> (area)<br>
            Blocked devices won't appear in any lists or be controllable.
          </div>
        </div>
      </div>
      
      <div class="ha-entities-preview">
        <div class="ha-entities-title">Visible Entities (after blacklist)</div>
        <div class="ha-entities-counts" id="ha-entities-counts">
          <em>Click "Refresh Preview" to check</em>
        </div>
        <button type="button" class="ha-fetch-btn" id="ha-fetch-entities">Refresh Preview</button>
      </div>
    </div>
  `;
  
  // Event listeners
  container.querySelector('#ha-test-btn').addEventListener('click', () => testConnection(container));
  container.querySelector('#ha-test-notify-btn').addEventListener('click', () => testNotify(container));
  container.querySelector('#ha-fetch-entities').addEventListener('click', () => fetchEntities(container));
  
  // Check token status on load
  checkTokenStatus(container);
}

async function saveSettings(settings) {
  // Extract token from settings (added by getFormSettings with underscore prefix)
  const token = settings._token;
  delete settings._token;  // Don't save token to settings file
  
  // Save token separately via credentials manager if provided
  if (token) {
    try {
      console.log('HA: Saving token to credentials manager');
      const res = await fetch('/api/webui/plugins/homeassistant/token', {
        method: 'PUT',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ token })
      });
      const data = await res.json();
      console.log('HA: Token save result:', data);
    } catch (e) {
      console.error('Failed to save HA token:', e);
    }
  }
  
  // Save other settings (without token)
  return pluginsAPI.saveSettings('homeassistant', settings);
}

function getFormSettings(container) {
  const getVal = (id) => {
    const el = container.querySelector(`#${id}`) || document.getElementById(id);
    return el?.value || '';
  };
  
  const blacklistText = getVal('ha-blacklist');
  const blacklist = blacklistText.split('\n').map(s => s.trim()).filter(Boolean);
  const token = getVal('ha-token').trim();
  const notifyService = getVal('ha-notify-service').trim();
  
  const settings = {
    url: getVal('ha-url').trim() || 'http://homeassistant.local:8123',
    blacklist: blacklist,
    notify_service: notifyService
  };
  
  // Include token with underscore prefix - saveSettings will extract and handle separately
  if (token) {
    settings._token = token;
  }
  
  return settings;
}

export default {
  name: 'homeassistant',
  
  init(container) {
    injectStyles();
    
    registerPluginSettings({
      id: 'homeassistant',
      name: 'Home Assistant',
      icon: '🏠',
      helpText: 'Connect to Home Assistant for smart home control. Create a Long-Lived Access Token in your HA profile settings.',
      render: renderForm,
      load: async () => {
        await loadDefaults();
        return pluginsAPI.getSettings('homeassistant');
      },
      save: saveSettings,
      getSettings: getFormSettings
    });
    
    console.log('✔ Home Assistant settings registered');
  },
  
  destroy() {
    // Nothing to clean up
  }
};