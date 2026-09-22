import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { renderSettingsForm, readSettingsForm } from '/static/shared/plugin-settings-renderer.js';
import pluginsAPI from '/static/shared/plugins-api.js';

const PLUGIN_NAME = 'discord';
const CSRF = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

// The shared renderer CSS.escape()s its id selectors, so dotted manifest keys
// (channel.reply_mode) work natively.
let _SCHEMA = [];

const DCG_STYLES = `
.dcg { font-family: inherit; color: var(--text); }
.dcg h4 { margin: 0 0 8px; font-size: 1rem; }
.dcg-section { margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
.dcg-row { display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; padding: 8px 0; }
.dcg-row label { font-weight: 500; }
.dcg-field { padding: 6px 0 10px; max-width: 520px; }
.dcg-field label { display: block; font-weight: 500; margin-bottom: 4px; }
.dcg-field .dcg-input { width: 100%; box-sizing: border-box; }
.dcg-help { font-size: 0.82em; color: var(--text-muted); margin-top: 2px; }
.dcg-input, .dcg-select, .dcg-textarea {
  background: var(--input-bg, var(--bg));
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  padding: 6px 10px;
  font: inherit;
}
.dcg-textarea { width: 100%; min-height: 72px; resize: vertical; }
.dcg-btn {
  border: 1px solid var(--border);
  background: var(--bg);
  color: var(--text);
  border-radius: 6px;
  padding: 6px 12px;
  cursor: pointer;
  font: inherit;
}
.dcg-btn-primary { background: var(--accent, #5865f2); color: #fff; border-color: transparent; }
.dcg-btn-danger { color: var(--error, #e74c3c); }
.dcg-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.dcg-account {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 0; border-bottom: 1px solid var(--border);
}
.dcg-badge { font-size: 0.75em; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); }
.dcg-badge-ok { color: var(--success); border-color: var(--success); }
.dcg-badge-off { color: var(--text-muted); }
.dcg-status { font-size: 0.85em; margin-top: 6px; }
.dcg-status-err { color: var(--error); }
.dcg-status-ok { color: var(--success); }
.dcg-notice {
  padding: 10px 12px; border-radius: 8px; margin-bottom: 16px;
  background: color-mix(in srgb, var(--accent, #5865f2) 12%, transparent);
  border: 1px solid var(--border);
  font-size: 0.9em;
}
.dcg-target-chips { display: flex; flex-wrap: wrap; gap: 6px; min-height: 28px; margin: 8px 0; }
.dcg-target-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 8px 3px 10px; border-radius: 12px;
  background: color-mix(in srgb, var(--accent, #5865f2) 15%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent, #5865f2) 35%, transparent);
  font-size: 0.78em;
}
.dcg-target-chip button {
  border: none; background: transparent; color: var(--text-muted);
  cursor: pointer; font-size: 1.1em; line-height: 1; padding: 0 2px;
}
.dcg-target-toolbar { display: flex; align-items: center; gap: 10px; margin: 8px 0; flex-wrap: wrap; }
.dcg-target-picker {
  max-height: 240px; overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 6px; padding: 8px 10px;
  background: var(--input-bg, var(--bg));
}
.dcg-target-group { margin-bottom: 10px; }
.dcg-target-group-title {
  font-size: 0.78em; font-weight: 600; color: var(--accent, #5865f2);
  margin-bottom: 4px;
}
.dcg-target-option {
  display: flex; align-items: center; gap: 8px;
  padding: 3px 0; font-size: 0.84em; cursor: pointer;
}
.dcg-debug-list { display: flex; flex-direction: column; gap: 10px; }
.dcg-debug-entry {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  background: color-mix(in srgb, var(--bg) 92%, var(--accent, #5865f2) 8%);
}
.dcg-debug-entry summary { cursor: pointer; font-weight: 600; }
.dcg-debug-meta { font-size: 0.82em; color: var(--text-muted); margin: 4px 0 8px; }
.dcg-debug-block {
  margin: 8px 0;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: var(--input-bg, var(--bg));
  font-size: 0.82em;
  white-space: pre-wrap;
  word-break: break-word;
}
.dcg-debug-label { font-size: 0.78em; font-weight: 600; color: var(--accent, #5865f2); margin-bottom: 4px; }
.dcg-debug-badge {
  display: inline-block; font-size: 0.72em; padding: 2px 7px; border-radius: 999px;
  border: 1px solid var(--border); margin-right: 6px;
}
.dcg-debug-badge-ok { color: var(--success); border-color: var(--success); }
.dcg-debug-badge-warn { color: var(--error); border-color: var(--error); }
.dcg-debug-badge-pending { color: var(--text-muted); }
`;

function esc(str) {
  const d = document.createElement('div');
  d.textContent = str ?? '';
  // textContent→innerHTML encodes & < > only. This helper also feeds attribute
  // values, where an unescaped quote breaks out of the attribute and runs an
  // inline handler with the owner's session (hunt H1).
  return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function api(path, options = {}) {
  const response = await fetch(`/api/plugin/${PLUGIN_NAME}/${path}`, {
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || body.detail || `HTTP ${response.status}`);
  return body;
}

function textarea(id, label, value, help = '') {
  return `<div style="margin:8px 0">
    <label for="${id}"><strong>${esc(label)}</strong></label>
    ${help ? `<div class="dcg-help">${esc(help)}</div>` : ''}
    <textarea class="dcg-textarea" id="${id}" data-field="${id}">${esc(value)}</textarea>
  </div>`;
}

function fieldByData(container, fieldId) {
  return [...container.querySelectorAll('[data-field]')].find((el) => el.dataset.field === fieldId) || null;
}

function daemonRunning(container) {
  return container.querySelector('.dcg-notice')?.textContent?.includes('Daemon is running');
}

// ── chip picker: a hidden list field fed by a refreshable checkbox list ─────
// Two instances: ignored channels (grouped by bot · server) and the bot allowlist.

class ChipPicker {
  constructor(container, opts) {
    this.container = container;
    this.opts = opts;               // { key, fieldId, load, group, label, selectAll }
    this.catalog = {};
    this.selection = new Set();
  }

  el(part) {
    return this.container.querySelector(`#dcg-${this.opts.key}-${part}`);
  }

  static markup(key, title, help, { selectAll = false, emptyText } = {}) {
    return `
      <div class="dcg-section">
        <h4>${esc(title)}</h4>
        <div class="dcg-help">${help}</div>
        <div id="dcg-${key}-chips" class="dcg-target-chips"><span class="dcg-help">None selected</span></div>
        <div class="dcg-target-toolbar">
          <button type="button" class="dcg-btn" id="dcg-${key}-refresh">Refresh from Discord</button>
          ${selectAll ? `<button type="button" class="dcg-btn" id="dcg-${key}-select-all">Select all</button>` : ''}
          <button type="button" class="dcg-btn" id="dcg-${key}-clear">Clear all</button>
          <span class="dcg-help" id="dcg-${key}-status"></span>
        </div>
        <div id="dcg-${key}-picker" class="dcg-target-picker">
          <p class="dcg-help" style="margin:0">${esc(emptyText)}</p>
        </div>
      </div>`;
  }

  init(selected) {
    this.selection = new Set((selected || []).map((line) => String(line).trim()).filter(Boolean));
    this.sync();
    this.el('refresh')?.addEventListener('click', () => this.load());
    this.el('select-all')?.addEventListener('click', () => this.setAll(true));
    this.el('clear')?.addEventListener('click', () => this.setAll(false));
    if (daemonRunning(this.container)) this.load();
  }

  values() {
    return [...this.selection].sort();
  }

  setAll(on) {
    this.container.querySelectorAll(`#dcg-${this.opts.key}-picker input[type="checkbox"]`).forEach((cb) => {
      cb.checked = on;
      if (on) this.selection.add(cb.dataset.value);
    });
    if (!on) this.selection.clear();
    this.sync();
  }

  sync() {
    const hidden = fieldByData(this.container, this.opts.fieldId);
    if (hidden) hidden.value = this.values().join('\n');
    const box = this.el('chips');
    if (!box) return;
    box.innerHTML = '';
    if (!this.selection.size) {
      box.innerHTML = '<span class="dcg-help">None selected</span>';
      return;
    }
    this.values().forEach((value) => {
      const chip = document.createElement('span');
      chip.className = 'dcg-target-chip';
      const text = document.createElement('span');
      text.textContent = this.catalog[value]?.label || value;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.setAttribute('aria-label', 'Remove');
      btn.textContent = '×';
      btn.addEventListener('click', () => {
        this.selection.delete(value);
        const cb = [...this.container.querySelectorAll(`#dcg-${this.opts.key}-picker input[type="checkbox"]`)]
          .find((el) => el.dataset.value === value);
        if (cb) cb.checked = false;
        this.sync();
      });
      chip.append(text, btn);
      box.appendChild(chip);
    });
  }

  render(items) {
    const box = this.el('picker');
    if (!box) return;
    box.innerHTML = '';
    if (!items.length) {
      box.innerHTML = `<p class="dcg-help" style="margin:0">${esc(this.opts.noneText)}</p>`;
      return;
    }
    const groups = {};
    items.forEach((item) => {
      this.catalog[item.value] = item;
      const title = this.opts.group(item);
      (groups[title] ||= []).push(item);
    });
    Object.entries(groups).forEach(([title, members]) => {
      const groupEl = document.createElement('div');
      groupEl.className = 'dcg-target-group';
      const titleEl = document.createElement('div');
      titleEl.className = 'dcg-target-group-title';
      titleEl.textContent = title;
      groupEl.appendChild(titleEl);
      members.forEach((item) => {
        const label = document.createElement('label');
        label.className = 'dcg-target-option';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.dataset.value = item.value;
        cb.checked = this.selection.has(item.value);
        cb.addEventListener('change', () => {
          if (cb.checked) this.selection.add(item.value);
          else this.selection.delete(item.value);
          this.sync();
        });
        const span = document.createElement('span');
        span.textContent = this.opts.label(item);
        label.append(cb, span);
        groupEl.appendChild(label);
      });
      box.appendChild(groupEl);
    });
    this.sync();
  }

  async load() {
    const status = this.el('status');
    const refreshBtn = this.el('refresh');
    if (refreshBtn) refreshBtn.disabled = true;
    if (status) status.textContent = 'Loading…';
    try {
      const { items, note } = await this.opts.load();
      this.render(items);
      if (status) status.textContent = note;
    } catch (err) {
      if (status) status.textContent = err.message || 'Failed to load';
    } finally {
      if (refreshBtn) refreshBtn.disabled = false;
    }
  }
}

let ignoredChannels = null;
let botAllowlist = null;

function buildPickers(container) {
  ignoredChannels = new ChipPicker(container, {
    key: 'ignore',
    fieldId: 'channel.ignored_channels',
    noneText: 'No text channels found. Connect a bot and ensure the daemon is running.',
    group: (t) => `${t.account} · ${t.guild_name}`,
    label: (t) => `#${t.channel_name}`,
    load: async () => {
      const data = await api('channels/text');
      if (data.error) throw new Error(data.error);
      const items = data.targets || [];
      return { items, note: data.connected ? `${items.length} channels` : 'Daemon offline / no bots connected' };
    },
  });
  botAllowlist = new ChipPicker(container, {
    key: 'bot-allowlist',
    fieldId: 'bot.allowlist_ids',
    noneText: 'No other bots found in connected servers. Bots must share a server with your bot and appear in the member list.',
    group: () => 'Bots in your servers',
    label: (b) => b.label,
    load: async () => {
      const data = await api('bots/allowlist');
      if (data.error && !data.bots?.length) throw new Error(data.error);
      const items = data.bots || [];
      return { items, note: items.length ? `${items.length} bot${items.length === 1 ? '' : 's'} found` : (data.error || 'No connected bots') };
    },
  });
}

// ── bot accounts ─────────────────────────────────────────────────────────────

function renderAccounts(container, accounts) {
  const list = container.querySelector('#dcg-accounts');
  if (!list) return;
  if (!accounts.length) {
    list.innerHTML = '<p class="dcg-help">No bot accounts configured yet.</p>';
    return;
  }
  list.innerHTML = accounts.map((a) => `
    <div class="dcg-account" data-account="${esc(a.name)}">
      <div>
        <strong>${esc(a.bot_name || a.name)}</strong>
        <span class="dcg-badge ${a.connected ? 'dcg-badge-ok' : 'dcg-badge-off'}">${a.connected ? 'connected' : (a.state === 'error' ? 'connect error' : a.state || 'disconnected')}</span>
        <div class="dcg-help">${esc(a.name)}${a.last_error ? ` — ${esc(a.last_error)}` : ''}</div>
        <div class="dcg-help dcg-test-result" data-name="${esc(a.name)}"></div>
      </div>
      <div style="display:flex;gap:8px">
        <button type="button" class="dcg-btn dcg-test-account" data-name="${esc(a.name)}">Test</button>
        <button type="button" class="dcg-btn dcg-btn-danger dcg-del-account" data-name="${esc(a.name)}">Remove</button>
      </div>
    </div>
  `).join('');
  const refresh = async () => {
    const refreshed = await api('accounts');
    renderAccounts(container, refreshed.accounts || []);
    return refreshed;
  };
  list.querySelectorAll('.dcg-test-account').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const result_for = (root) => [...root.querySelectorAll('.dcg-test-result')].find((el) => el.dataset.name === btn.dataset.name);
      const out = result_for(list);
      btn.disabled = true;
      if (out) out.textContent = 'Testing token against Discord…';
      try {
        const result = await api(`accounts/${encodeURIComponent(btn.dataset.name)}/test`, { method: 'POST' });
        const suffix = result.reconnect
          ? ' — token OK, retrying connection…'
          : ' (token check only — the badge shows connection state)';
        const okText = `✓ ${result.message}${suffix}`;
        if (out) out.textContent = result.success ? okText : `✗ ${result.error || 'Test failed'}`;
        if (result.success) {
          await refresh();
          const fresh = result_for(container);
          if (fresh) fresh.textContent = okText;
          if (result.reconnect) setTimeout(() => refresh().catch(() => {}), 4000);
        }
      } catch (e) {
        if (out) out.textContent = `✗ ${e.message}`;
      } finally {
        btn.disabled = false;
      }
    });
  });
  list.querySelectorAll('.dcg-del-account').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm(`Remove bot "${btn.dataset.name}"?`)) return;
      btn.disabled = true;
      try {
        await api(`accounts/${encodeURIComponent(btn.dataset.name)}`, { method: 'DELETE' });
        await refresh();
      } catch (e) {
        alert(e.message);
        btn.disabled = false;
      }
    });
  });
}

function bindAccounts(container) {
  const form = container.querySelector('#dcg-add-form');
  const status = container.querySelector('#dcg-acc-status');
  const say = (text, cls = '') => {
    status.textContent = text;
    status.className = `dcg-status ${cls}`;
  };
  container.querySelector('#dcg-add-toggle')?.addEventListener('click', () => {
    form.style.display = form.style.display === 'none' ? 'block' : 'none';
  });
  container.querySelector('#dcg-acc-test')?.addEventListener('click', async () => {
    const token = container.querySelector('#dcg-acc-token')?.value?.trim();
    const btn = container.querySelector('#dcg-acc-test');
    if (!token) return say('Paste a token to test.', 'dcg-status-err');
    btn.disabled = true;
    say('Testing token against Discord…');
    try {
      const result = await api('accounts/test', { method: 'POST', body: JSON.stringify({ token }) });
      say(result.success ? `✓ ${result.message}` : `✗ ${result.error || 'Test failed'}`,
        result.success ? 'dcg-status-ok' : 'dcg-status-err');
    } catch (e) {
      say(`✗ ${e.message}`, 'dcg-status-err');
    } finally {
      btn.disabled = false;
    }
  });
  container.querySelector('#dcg-acc-save')?.addEventListener('click', async () => {
    const name = container.querySelector('#dcg-acc-name')?.value?.trim();
    const token = container.querySelector('#dcg-acc-token')?.value?.trim();
    const btn = container.querySelector('#dcg-acc-save');
    if (!name || !token) return say('Name and token required.', 'dcg-status-err');
    btn.disabled = true;
    say('Saving…');
    try {
      await api('accounts', { method: 'POST', body: JSON.stringify({ account_name: name, token }) });
      say('Account saved.', 'dcg-status-ok');
      const refreshed = await api('accounts');
      renderAccounts(container, refreshed.accounts || []);
      form.style.display = 'none';
      container.querySelector('#dcg-acc-name').value = '';
      container.querySelector('#dcg-acc-token').value = '';
    } catch (e) {
      say(e.message, 'dcg-status-err');
    } finally {
      btn.disabled = false;
    }
  });
}

// ── LLM debug panel (Debug tab slot) ──────────────────────────────────────────

let _debugRefreshTimer = null;

function debugStatusBadge(status) {
  const normalized = String(status || 'pending').toLowerCase();
  if (normalized === 'sent') return '<span class="dcg-debug-badge dcg-debug-badge-ok">sent</span>';
  if (normalized === 'pending') return '<span class="dcg-debug-badge dcg-debug-badge-pending">pending</span>';
  return `<span class="dcg-debug-badge dcg-debug-badge-warn">${esc(normalized)}</span>`;
}

function formatDebugTimestamp(ts) {
  if (!ts) return '—';
  try {
    return new Date(Number(ts) * 1000).toLocaleString();
  } catch {
    return '—';
  }
}

function formatLlmSummary(llm) {
  const resolved = llm || {};
  const configured = [resolved.configured_primary, resolved.configured_model].filter(Boolean).join(' / ') || 'auto';
  return resolved.task_name ? `${configured} · task: ${resolved.task_name}` : configured;
}

function renderDebugEntries(listEl, entries) {
  if (!listEl) return;
  if (!entries.length) {
    listEl.innerHTML = '<p class="dcg-help" style="margin:0">No LLM exchanges yet — send a message to the bot while the daemon is running.</p>';
    return;
  }
  listEl.innerHTML = entries.map((entry) => {
    const prompt = entry.prompt || {};
    const response = entry.response || {};
    const delivery = entry.delivery || {};
    const timing = entry.timing || {};
    const trigger = entry.trigger || {};
    const rejection = entry.rejection || {};
    const location = [entry.account, entry.guild_name || entry.guild_id, entry.channel_name || entry.channel_id]
      .filter(Boolean).join(' · ');
    const latency = timing.latency_ms != null ? `${timing.latency_ms} ms` : '—';

    if (entry.kind === 'rejection') {
      return `
        <details class="dcg-debug-entry" open>
          <summary><span class="dcg-debug-badge dcg-debug-badge-warn">rejected</span> ${esc(formatDebugTimestamp(entry.created_at))} · ${esc(rejection.stage || 'policy')}</summary>
          <div class="dcg-debug-meta">
            ${esc(location)} · from ${esc(trigger.username || '—')} · reason: ${esc(rejection.reason || trigger.reason || 'blocked')}
          </div>
          <div class="dcg-debug-label">Trigger message</div>
          <div class="dcg-debug-block">${esc(trigger.content || '—')}</div>
          <div class="dcg-debug-label">Why it never reached the LLM</div>
          <div class="dcg-debug-block">Stage: ${esc(rejection.stage || 'policy')}
Reason: ${esc(rejection.reason || 'blocked')}${Object.keys(rejection.detail || {}).length ? `\nDetail: ${esc(JSON.stringify(rejection.detail, null, 2))}` : ''}</div>
        </details>`;
    }

    const sentText = delivery.sent_text || (response.parsed_chunks || [])[0] || '';
    const history = prompt.recent_history || '';
    const hints = (prompt.reply_hints || []).join('\n\n');
    return `
      <details class="dcg-debug-entry" open>
        <summary>${debugStatusBadge(response.status)} ${esc(formatDebugTimestamp(entry.created_at))} · ${esc(entry.source || 'discord_message')}</summary>
        <div class="dcg-debug-meta">
          ${esc(location)} · trigger: ${esc(trigger.reason || trigger.username || '—')} · ${esc(formatLlmSummary(entry.llm))} · latency: ${esc(latency)}
        </div>
        <div class="dcg-debug-label">Trigger message</div>
        <div class="dcg-debug-block">${esc(trigger.content || '—')}</div>
        <div class="dcg-debug-label">Prompt — user content</div>
        <div class="dcg-debug-block">${esc(prompt.user_content || '—')}</div>
        ${history ? `<div class="dcg-debug-label">Prompt — recent history</div><div class="dcg-debug-block">${esc(history)}</div>` : ''}
        ${hints ? `<div class="dcg-debug-label">Prompt — reply hints</div><div class="dcg-debug-block">${esc(hints)}</div>` : ''}
        <div class="dcg-debug-label">LLM response (raw)</div>
        <div class="dcg-debug-block">${esc(response.raw || '—')}</div>
        ${(response.parsed_chunks || []).length > 1 ? `<div class="dcg-debug-label">Parsed chunks</div><div class="dcg-debug-block">${esc((response.parsed_chunks || []).join('\n---\n'))}</div>` : ''}
        <div class="dcg-debug-label">Delivery</div>
        <div class="dcg-debug-block">Sent: ${esc(sentText)}
${response.strip_think_tags != null ? `\nThink tags stripped: ${response.strip_think_tags ? 'yes' : 'no'}` : ''}
${delivery.quote_reply_to ? `\nQuote-reply to: ${esc(delivery.quote_reply_to)}` : ''}
Chunks sent: ${Number(delivery.chunks_sent) || 0}</div>
      </details>`;
  }).join('');
}

async function refreshDebugPanel(container) {
  const list = container.querySelector('#dcg-debug-list');
  const status = container.querySelector('#dcg-debug-status');
  if (!list) return;
  if (status) status.textContent = 'Refreshing…';
  try {
    const data = await api('debug/llm?limit=10');
    renderDebugEntries(list, data.entries || []);
    if (status) {
      status.textContent = `Updated ${new Date().toLocaleTimeString()} · daemon ${data.daemon_running ? 'running' : 'offline'}`;
    }
  } catch (err) {
    if (status) status.textContent = err.message;
    list.innerHTML = `<p class="dcg-help" style="margin:0;color:var(--error)">${esc(err.message)}</p>`;
  }
}

function initDebugPanel(container, initialEntries = []) {
  const list = container.querySelector('#dcg-debug-list');
  if (!list) return;
  renderDebugEntries(list, initialEntries);
  container.querySelector('#dcg-debug-refresh')?.addEventListener('click', () => refreshDebugPanel(container));
  container.querySelector('#dcg-debug-clear')?.addEventListener('click', async () => {
    try {
      await api('debug/clear', { method: 'POST', body: {} });
    } catch (err) {
      const status = container.querySelector('#dcg-debug-status');
      if (status) status.textContent = err.message;
      return;
    }
    refreshDebugPanel(container);
  });
  if (_debugRefreshTimer) clearInterval(_debugRefreshTimer);
  _debugRefreshTimer = setInterval(() => {
    if (container.isConnected) refreshDebugPanel(container);
  }, 15000);
}

// ── voice channel list (Voice tab slot) ───────────────────────────────────────

async function loadVoiceChannelList(container) {
  const status = container.querySelector('#dcg-voice-channels-status');
  const list = container.querySelector('#dcg-voice-channels-list');
  const refreshBtn = container.querySelector('#dcg-voice-channels-refresh');
  if (!list) return;
  if (refreshBtn) refreshBtn.disabled = true;
  if (status) status.textContent = 'Loading…';
  try {
    const data = await api('voice/targets');
    if (data.error && !data.targets?.length) throw new Error(data.error);
    const targets = data.targets || [];
    list.innerHTML = targets.length
      ? targets.map((t) => `<div class="dcg-row"><span>${esc(t.label || t.channel_name || t.value)}</span><code>${esc(t.channel_id || t.value)}</code></div>`).join('')
      : '<p class="dcg-help" style="margin:0">No voice channels visible to the connected bots.</p>';
    if (status) status.textContent = `${targets.length} voice channel${targets.length === 1 ? '' : 's'}`;
  } catch (err) {
    if (status) status.textContent = err.message || 'Failed to load voice channels';
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

// ── the shell ─────────────────────────────────────────────────────────────────

function renderShell(container, data) {
  const schema = data.schema || [];
  const values = data.values || {};
  const voicePromptDefault = data.settings?.defaults?.voice?.conversation_prompt_template || '';
  const daemon = data.settings?.daemon_running;
  const daemonState = data.settings?.daemon_state || data.health?.state || 'unknown';
  const daemonNote = !daemon
    ? `<div class="dcg-notice">Daemon is offline (${esc(daemonState)}). Enable the plugin under Settings → Plugins, then reload it. You do not add a separate daemon — the plugin starts its own runtime.</div>`
    : `<div class="dcg-notice" style="border-color:var(--success)">Daemon is running (${esc(daemonState)}).</div>`;

  container.innerHTML = `
    <style>${DCG_STYLES}</style>
    <div class="dcg">
      ${daemonNote}
      <div class="dcg-section">
        <h4>Bot Accounts</h4>
        <p class="dcg-help">Create a bot at discord.com/developers, enable the <strong>Message Content</strong> and <strong>Server Members</strong> intents (Bot page), then paste its token here. A saved bot logs in only when an enabled daemon task (Settings → Continuity) selects it.</p>
        <div id="dcg-accounts"></div>
        <div style="margin-top:8px;display:flex;gap:8px">
          <button type="button" class="dcg-btn" id="dcg-add-toggle">+ Add Bot</button>
        </div>
        <div id="dcg-add-form" style="display:none;margin-top:12px">
          <div class="dcg-field">
            <label for="dcg-acc-name">Account name</label>
            <input class="dcg-input" id="dcg-acc-name" placeholder="e.g. sapphire">
            <div class="dcg-help">Sapphire's local label for this bot — daemon tasks key off it. Any short name; it does not need to match the bot's Discord username.</div>
          </div>
          <div class="dcg-field">
            <label for="dcg-acc-token">Bot token</label>
            <input class="dcg-input" id="dcg-acc-token" type="password" placeholder="paste bot token">
            <div class="dcg-help">Discord Developer Portal → your app → Bot → Reset Token.</div>
          </div>
          <div style="display:flex;gap:8px">
            <button type="button" class="dcg-btn" id="dcg-acc-test">Test token</button>
            <button type="button" class="dcg-btn dcg-btn-primary" id="dcg-acc-save">Add Bot</button>
          </div>
          <div class="dcg-status" id="dcg-acc-status"></div>
        </div>
      </div>
      <div id="dcg-schema-form"></div>
    </div>
  `;

  const ignoredChannelsList = values['channel.ignored_channels'] || [];
  const allowlistIds = values['bot.allowlist_ids'] || [];
  const conversationSections = ChipPicker.markup('ignore', 'Ignored channels',
    'Fully ignore these text channels: no replies, reactions, or scheduled posts. Save after changing.',
    { emptyText: 'Connect a bot and refresh to list channels.' })
    + `<textarea class="dcg-textarea" id="channel.ignored_channels" data-field="channel.ignored_channels" style="display:none" aria-hidden="true">${esc(ignoredChannelsList.join('\n'))}</textarea>`
    + ChipPicker.markup('bot-allowlist', 'Allowlisted Bots',
      'Bots she may answer (Bot-to-bot replies must be on). Loaded from servers your bot is in — enable <strong>Server Members Intent</strong> in the Developer Portal or the list stays empty.',
      { selectAll: true, emptyText: 'Connect a bot, then click Refresh to load bots.' })
    + `<textarea class="dcg-textarea" id="bot.allowlist_ids" data-field="bot.allowlist_ids" style="display:none" aria-hidden="true">${esc(allowlistIds.join('\n'))}</textarea>`;

  const voiceSections = `
      <div class="dcg-section">
        <h4>Voice channels</h4>
        <div class="dcg-help">Voice is a Realtime rule: Settings → Continuity → Realtime → <strong>Discord: Voice channel</strong> names the bot; its filter says which voice channels the rule covers (server or channel name, or an id from the list below — no filter = all of them), <strong>Auto-join</strong> is opt-in per rule, and its persona, model and toolset run the voice chat. She joins on <code>/voice join</code> or her join tool, and leaves on &lt;&lt;HANG UP&gt;&gt;, <code>/voice leave</code>, or when the channel empties.</div>
        <div class="dcg-target-toolbar">
          <button type="button" class="dcg-btn" id="dcg-voice-channels-refresh">Refresh from Discord</button>
          <span class="dcg-help" id="dcg-voice-channels-status"></span>
        </div>
        <div id="dcg-voice-channels-list" class="dcg-target-picker">
          <p class="dcg-help" style="margin:0">Connect a bot, then click Refresh to list voice channels.</p>
        </div>
      </div>
      <div class="dcg-section">
        <h4>Voice Conversation Prompt</h4>
        ${textarea(
          'voice.conversation_prompt_template',
          'Voice conversation prompt',
          values['voice.conversation_prompt_template'] || voicePromptDefault,
          'System instructions for conversational voice mode. Placeholders: {primary} = bot name, {alias_line} = alias suffix (empty when none). Clear and save to restore the built-in default.',
        )}
      </div>`;

  const debugSection = `
      <div class="dcg-section">
        <h4>LLM Debug</h4>
        <p class="dcg-help">Last 10 LLM-related events — successful exchanges and policy rejections. Shows the task's model, the prompt breakdown, and why blocked messages never reached the LLM. Held in memory only while the ring is on.</p>
        <div class="dcg-target-toolbar">
          <button type="button" class="dcg-btn" id="dcg-debug-refresh">Refresh now</button>
          <button type="button" class="dcg-btn" id="dcg-debug-clear">Clear</button>
          <span class="dcg-help" id="dcg-debug-status"></span>
        </div>
        <div id="dcg-debug-list" class="dcg-debug-list">
          <p class="dcg-help" style="margin:0">Loading…</p>
        </div>
      </div>`;

  const slots = [
    { tab: 'Conversation', mount: (el) => { el.innerHTML = conversationSections; } },
    { tab: 'Voice', mount: (el) => { el.innerHTML = voiceSections; } },
    { tab: 'Debug', mount: (el) => { el.innerHTML = debugSection; } },
  ];

  const settingsBox = container.querySelector('#dcg-schema-form');
  if (!schema.length) {
    settingsBox.insertAdjacentHTML('beforebegin',
      '<div class="dcg-notice">Settings schema unavailable — plugin manifest not loaded. Bot accounts and the widget tabs still work; reload the plugin to restore the settings fields.</div>');
  }
  renderSettingsForm(settingsBox, schema, values, { slots });

  renderAccounts(container, data.accounts?.accounts || []);
  bindAccounts(container);
  const voicePromptField = fieldByData(container, 'voice.conversation_prompt_template');
  if (voicePromptField && voicePromptDefault) voicePromptField.dataset.defaultTemplate = voicePromptDefault;
  initDebugPanel(container, data.llmDebug?.entries || []);
  buildPickers(container);
  ignoredChannels.init(ignoredChannelsList);
  botAllowlist.init(allowlistIds);
  container.querySelector('#dcg-voice-channels-refresh')?.addEventListener('click', () => loadVoiceChannelList(container));
  if (daemonRunning(container)) loadVoiceChannelList(container);
}

function normalizedVoicePrompt(container) {
  const field = fieldByData(container, 'voice.conversation_prompt_template');
  if (!field) return '';
  const value = field.value || '';
  const defaultTemplate = field.dataset.defaultTemplate || '';
  if (defaultTemplate && value.trim() === defaultTemplate.trim()) return '';
  return value;
}

// Flat dotted keys owned by the slot-mounted widget sections.
function customSectionValues(container) {
  return {
    'channel.ignored_channels': ignoredChannels ? ignoredChannels.values() : [],
    'bot.allowlist_ids': botAllowlist ? botAllowlist.values() : [],
    'voice.conversation_prompt_template': normalizedVoicePrompt(container),
  };
}

async function loadPanelData() {
  const [accounts, settings, health, llmDebug, plugins, values] = await Promise.allSettled([
    api('accounts'),
    api('settings'), // daemon state + built-in defaults only — values live in core
    api('health'),
    api('debug/llm?limit=10'),
    pluginsAPI.listPlugins(),
    pluginsAPI.getSettings(PLUGIN_NAME),
  ]);
  const val = (r, fallback = {}) => (r.status === 'fulfilled' ? r.value : fallback);
  const settingsData = val(settings, {});
  const healthData = val(health, {});
  const running = settingsData.daemon_running === true || healthData.daemon_running === true
    || healthData.state === 'ready' || healthData.state === 'starting';
  _SCHEMA = (val(plugins, {}).plugins || []).find((p) => p.name === PLUGIN_NAME)?.settings_schema || [];
  return {
    accounts: val(accounts, { accounts: [] }),
    schema: _SCHEMA,
    values: val(values, {}),
    settings: {
      defaults: settingsData.defaults || {},
      daemon_running: running,
      daemon_state: settingsData.daemon_state || healthData.state || 'unknown',
    },
    health: healthData,
    llmDebug: val(llmDebug, { entries: [] }),
  };
}

function registerTab() {
  registerPluginSettings({
    id: PLUGIN_NAME,
    name: 'Discord',
    icon: '🎮',
    helpText: 'Discord bot accounts, conversation behavior, reactions, safety, media, retention and voice knobs. Chat, greetings and voice are Continuity tasks; personality modules live in the discord-personality plugin.',

    load: () => loadPanelData(),

    render(container, data) {
      try {
        renderShell(container, data || {});
      } catch (err) {
        console.error('[discord] settings render failed:', err);
        container.innerHTML = `<p style="color:var(--error)">Failed to render settings: ${esc(err.message)}</p>`;
      }
    },

    getSettings(container) {
      // Wipe guard: customSectionValues returns plausible EMPTIES when the slot
      // sections aren't in the DOM — saving that would silently wipe stored
      // keys. If the panel hasn't finished rendering, refuse; the settings page
      // surfaces this as "Save failed: …" instead of a lying success toast.
      const box = container.querySelector('#dcg-schema-form');
      if (!box) throw new Error('Discord panel is still loading — wait a moment and save again.');
      const schemaValues = _SCHEMA.length ? readSettingsForm(box, _SCHEMA) : {};
      return { ...schemaValues, ...customSectionValues(container) };
    },

    save: (s) => pluginsAPI.saveSettings(PLUGIN_NAME, s),
  });
}

registerTab();

document.addEventListener('sapphire:plugin_toggled', (event) => {
  const detail = event.detail || {};
  const name = detail.plugin || detail.name;
  if (name === PLUGIN_NAME && detail.enabled) registerTab();
});

export default { init() { registerTab(); } };
