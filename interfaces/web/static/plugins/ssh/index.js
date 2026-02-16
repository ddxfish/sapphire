// index.js - SSH settings plugin
// Settings tab in Plugins modal for SSH server management

import { registerPluginSettings } from '../plugins-modal/plugin-registry.js';
import pluginsAPI from '../plugins-modal/plugins-api.js';

const DEFAULT_BLACKLIST = [
  'rm -rf /',
  'rm -rf /*',
  '--no-preserve-root',
  'mkfs',
  'dd if=/dev',
  ':(){ :|:& };:',
  '> /dev/sda',
  'chmod -R 777 /',
  'init 0',
  'init 6',
];

let servers = [];

function injectStyles() {
  if (document.getElementById('ssh-plugin-styles')) return;

  const style = document.createElement('style');
  style.id = 'ssh-plugin-styles';
  style.textContent = `
    .ssh-form {
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .ssh-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .ssh-group label {
      font-size: 13px;
      font-weight: 500;
      color: var(--text);
    }

    .ssh-group input,
    .ssh-group textarea {
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg-primary);
      color: var(--text);
      font-size: 13px;
      font-family: inherit;
    }

    .ssh-group input:focus,
    .ssh-group textarea:focus {
      outline: none;
      border-color: var(--accent-blue);
    }

    .ssh-group textarea {
      resize: vertical;
      min-height: 80px;
      font-family: monospace;
      font-size: 12px;
    }

    .ssh-hint {
      font-size: 11px;
      color: var(--text-muted);
      margin-top: 4px;
    }

    .ssh-section {
      border-top: 1px solid var(--border);
      padding-top: 16px;
      margin-top: 8px;
    }

    .ssh-section-title {
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 12px;
    }

    .ssh-server-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .ssh-server-row {
      display: flex;
      gap: 8px;
      align-items: center;
      padding: 8px 12px;
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-radius: 6px;
    }

    .ssh-server-info {
      flex: 1;
      font-size: 13px;
    }

    .ssh-server-name {
      font-weight: 600;
      color: var(--text);
    }

    .ssh-server-detail {
      color: var(--text-muted);
      font-size: 12px;
    }

    .ssh-btn {
      padding: 6px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--bg-tertiary);
      color: var(--text);
      cursor: pointer;
      font-size: 12px;
      transition: all 0.15s ease;
    }

    .ssh-btn:hover {
      background: var(--bg-hover);
    }

    .ssh-btn.danger {
      border-color: var(--error, #dc3545);
      color: var(--error, #dc3545);
    }

    .ssh-btn.danger:hover {
      background: var(--error-light, #f8d7da);
    }

    .ssh-btn.success {
      background: var(--success-light, #d4edda);
      border-color: var(--success, #28a745);
      color: var(--success, #28a745);
    }

    .ssh-btn.error {
      background: var(--error-light, #f8d7da);
      border-color: var(--error, #dc3545);
      color: var(--error, #dc3545);
    }

    .ssh-add-form {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      padding: 12px;
      background: var(--bg-secondary);
      border: 1px dashed var(--border);
      border-radius: 6px;
    }

    .ssh-add-form input {
      padding: 6px 10px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--bg-primary);
      color: var(--text);
      font-size: 13px;
    }

    .ssh-add-form input:focus {
      outline: none;
      border-color: var(--accent-blue);
    }

    .ssh-add-actions {
      grid-column: 1 / -1;
      display: flex;
      gap: 8px;
    }

    .ssh-settings-row {
      display: flex;
      gap: 16px;
      align-items: flex-end;
    }

    .ssh-settings-row .ssh-group {
      flex: 1;
    }

    .ssh-settings-row input {
      width: 100%;
    }
  `;
  document.head.appendChild(style);
}


function renderServerList(container) {
  const listEl = container.querySelector('#ssh-server-list');
  if (!listEl) return;

  if (!servers.length) {
    listEl.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:8px">No servers configured</div>';
    return;
  }

  listEl.innerHTML = servers.map((s, i) => `
    <div class="ssh-server-row" data-idx="${i}">
      <div class="ssh-server-info">
        <div class="ssh-server-name">${esc(s.name)}</div>
        <div class="ssh-server-detail">${esc(s.user)}@${esc(s.host)}:${s.port || 22} &middot; ${esc(s.key_path || '~/.ssh/id_ed25519')}</div>
      </div>
      <button class="ssh-btn ssh-test-server" data-idx="${i}">Test</button>
      <button class="ssh-btn danger ssh-del-server" data-idx="${i}">&times;</button>
    </div>
  `).join('');

  // Test buttons
  listEl.querySelectorAll('.ssh-test-server').forEach(btn => {
    btn.addEventListener('click', async () => {
      const s = servers[parseInt(btn.dataset.idx)];
      btn.disabled = true;
      btn.textContent = '...';
      try {
        const res = await fetch('/api/webui/plugins/ssh/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(s)
        });
        const data = await res.json();
        if (data.success) {
          btn.textContent = '\u2713';
          btn.className = 'ssh-btn success';
        } else {
          btn.textContent = '\u2717';
          btn.className = 'ssh-btn error';
          btn.title = data.error || 'Failed';
        }
      } catch (e) {
        btn.textContent = '\u2717';
        btn.className = 'ssh-btn error';
      }
      btn.disabled = false;
      setTimeout(() => {
        btn.textContent = 'Test';
        btn.className = 'ssh-btn ssh-test-server';
        btn.title = '';
      }, 4000);
    });
  });

  // Delete buttons
  listEl.querySelectorAll('.ssh-del-server').forEach(btn => {
    btn.addEventListener('click', () => {
      servers.splice(parseInt(btn.dataset.idx), 1);
      renderServerList(container);
    });
  });
}


function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}


function renderForm(container, settings) {
  const s = settings || {};
  const blacklist = s.blacklist || DEFAULT_BLACKLIST;
  const blacklistText = Array.isArray(blacklist) ? blacklist.join('\n') : blacklist;

  container.innerHTML = `
    <div class="ssh-form">
      <div class="ssh-section-title">Servers</div>
      <div class="ssh-server-list" id="ssh-server-list"></div>

      <div class="ssh-add-form" id="ssh-add-form">
        <input type="text" id="ssh-new-name" placeholder="Friendly name *">
        <input type="text" id="ssh-new-host" placeholder="Host / IP *">
        <input type="text" id="ssh-new-user" placeholder="Username *" value="root">
        <input type="number" id="ssh-new-port" placeholder="Port" value="22" min="1" max="65535">
        <input type="text" id="ssh-new-key" placeholder="Key path (default: ~/.ssh/id_ed25519)">
        <div class="ssh-add-actions">
          <button class="ssh-btn" id="ssh-add-btn">+ Add Server</button>
        </div>
      </div>

      <div class="ssh-section">
        <div class="ssh-section-title">Settings</div>
        <div class="ssh-settings-row">
          <div class="ssh-group">
            <label for="ssh-output-limit">Output Limit (chars)</label>
            <input type="number" id="ssh-output-limit" value="${s.output_limit || 6000}" min="500" max="50000">
          </div>
          <div class="ssh-group">
            <label for="ssh-max-timeout">Max Timeout (seconds)</label>
            <input type="number" id="ssh-max-timeout" value="${s.max_timeout || 120}" min="10" max="600">
          </div>
        </div>
      </div>

      <div class="ssh-section">
        <div class="ssh-section-title">Command Blacklist</div>
        <div class="ssh-group">
          <label for="ssh-blacklist">Blocked patterns (one per line)</label>
          <textarea id="ssh-blacklist" rows="8" placeholder="rm -rf /\nmkfs\ndd if=/dev">${esc(blacklistText)}</textarea>
          <div class="ssh-hint">
            Each line is checked as a regex pattern against commands. Simple strings work too.<br>
            Example: <code>rm -rf /</code> (literal), <code>rm\\s+-rf\\s+/</code> (regex)
          </div>
        </div>
      </div>
    </div>
  `;

  renderServerList(container);

  // Add server button
  container.querySelector('#ssh-add-btn').addEventListener('click', () => {
    const getVal = (id) => (container.querySelector(`#${id}`) || document.getElementById(id))?.value?.trim() || '';
    const name = getVal('ssh-new-name');
    const host = getVal('ssh-new-host');
    const user = getVal('ssh-new-user');
    const port = parseInt(getVal('ssh-new-port')) || 22;
    const key_path = getVal('ssh-new-key') || '~/.ssh/id_ed25519';

    if (!name || !host || !user) {
      alert('Name, host, and username are required');
      return;
    }

    // Check for duplicate name
    if (servers.some(s => s.name.toLowerCase() === name.toLowerCase())) {
      alert(`Server "${name}" already exists`);
      return;
    }

    servers.push({ name, host, user, port, key_path });
    renderServerList(container);

    // Clear form
    const form = container.querySelector('#ssh-add-form');
    form.querySelector('#ssh-new-name').value = '';
    form.querySelector('#ssh-new-host').value = '';
    form.querySelector('#ssh-new-user').value = 'root';
    form.querySelector('#ssh-new-port').value = '22';
    form.querySelector('#ssh-new-key').value = '';
  });
}


async function saveSettings(settings) {
  // Save servers to credentials manager
  try {
    await fetch('/api/webui/plugins/ssh/servers', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ servers })
    });
  } catch (e) {
    console.error('Failed to save SSH servers:', e);
  }

  // Save settings (output_limit, max_timeout, blacklist) to plugin settings
  const pluginSettings = {
    output_limit: settings._output_limit,
    max_timeout: settings._max_timeout,
    blacklist: settings._blacklist,
  };
  return pluginsAPI.saveSettings('ssh', pluginSettings);
}


function getFormSettings(container) {
  const getVal = (id) => {
    const el = container.querySelector(`#${id}`) || document.getElementById(id);
    return el?.value || '';
  };

  const blacklistText = getVal('ssh-blacklist');
  const blacklist = blacklistText.split('\n').map(s => s.trim()).filter(Boolean);

  return {
    _output_limit: parseInt(getVal('ssh-output-limit')) || 6000,
    _max_timeout: parseInt(getVal('ssh-max-timeout')) || 120,
    _blacklist: blacklist,
  };
}


export default {
  name: 'ssh',

  init(container) {
    injectStyles();

    registerPluginSettings({
      id: 'ssh',
      name: 'SSH',
      icon: '\uD83D\uDDA5\uFE0F',
      helpText: 'Configure SSH servers for remote command execution. The AI uses server friendly names to avoid mix-ups. Private keys stay in ~/.ssh/ — only the path is stored.',
      render: renderForm,
      load: async () => {
        // Load servers from credentials + settings from plugin settings
        try {
          const [serversRes, settingsData] = await Promise.all([
            fetch('/api/webui/plugins/ssh/servers').then(r => r.json()),
            pluginsAPI.getSettings('ssh'),
          ]);
          servers = serversRes.servers || [];
          return settingsData || {};
        } catch (e) {
          console.warn('Failed to load SSH settings:', e);
          servers = [];
          return {};
        }
      },
      save: saveSettings,
      getSettings: getFormSettings
    });

    console.log('\u2714 SSH settings registered');
  },

  destroy() {}
};
