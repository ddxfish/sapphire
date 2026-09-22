import { registerPluginSettings } from '/static/shared/plugin-registry.js';
import { renderSettingsForm, readSettingsForm } from '/static/shared/plugin-settings-renderer.js';
import pluginsAPI from '/static/shared/plugins-api.js';

const PLUGIN_NAME = 'discord';
const CSRF = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

// The old '--' dot-bridge is gone: the shared renderer now CSS.escape()s its
// id selectors, so dotted manifest keys (channel.reply_mode) work natively.

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
.dcg-details summary { cursor: pointer; font-weight: 500; margin-bottom: 8px; }
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
  // values (data-content="…"), where an unescaped quote breaks out of the
  // attribute and runs an inline handler with the owner's session (hunt H1).
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

function renderShell(container, data) {
  const schema = data.schema || [];
  const values = data.values || {};
  const defaults = data.settings?.defaults || {};
  const voicePromptDefault = defaults.voice?.conversation_prompt_template || '';
  const daemon = data.settings?.daemon_running;
  const daemonState = data.settings?.daemon_state || data.health?.state || 'unknown';
  const daemonNote = !daemon
    ? `<div class="dcg-notice">Daemon is offline (${esc(daemonState)}). Enable the plugin under Settings → Plugins, then reload it. You do not add a separate daemon — this plugin starts one automatically when enabled.</div>`
    : `<div class="dcg-notice" style="border-color:var(--success)">Daemon is running (${esc(daemonState)}).</div>`;

  const ignoredChannels = values['channel.ignored_channels'] || [];
  const allowlistIds = values['bot.allowlist_ids'] || [];

  container.innerHTML = `
    <style>${DCG_STYLES}</style>
    <div class="dcg">
      ${daemonNote}

      <div class="dcg-section">
        <h4>Bot Accounts</h4>
        <p class="dcg-help">Create a bot at discord.com/developers, enable the <strong>Message Content</strong> and <strong>Server Members</strong> intents (Bot page), then paste the token.</p>
        <div id="dcg-accounts"></div>
        <div style="margin-top:8px;display:flex;gap:8px">
          <button type="button" class="dcg-btn" id="dcg-add-toggle">+ Add Bot</button>
        </div>
        <div id="dcg-add-form" style="display:none;margin-top:12px">
          <div class="dcg-field">
            <label for="dcg-acc-name">Account name</label>
            <input class="dcg-input" id="dcg-acc-name" placeholder="e.g. sapphire">
            <div class="dcg-help">Sapphire's local label for this bot — daemon tasks and per-bot settings key off it. Any short name; it does not need to match the bot's Discord username.</div>
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

      <details class="dcg-details" style="margin-top:20px">
        <summary>Operator debug</summary>
        <pre id="dcg-debug" style="font-size:0.8em;overflow:auto;max-height:240px"></pre>
      </details>
    </div>
  `;

  // Custom widget sections — mounted into renderer slots on the tab that owns
  // their settings (this was the always-visible pile below the schema form).
  const allowlistSection = `
      <div class="dcg-section">
        <h4>Ignored channels</h4>
        <p class="dcg-help">Fully ignore these text channels: no replies, reactions, or scheduled posts. Save after changing.</p>
        <div id="dcg-ignore-chips" class="dcg-target-chips"><span class="dcg-help">None selected</span></div>
        <div class="dcg-target-toolbar">
          <button type="button" class="dcg-btn" id="dcg-ignore-refresh">Refresh from Discord</button>
          <button type="button" class="dcg-btn" id="dcg-ignore-clear">Clear all</button>
          <span class="dcg-help" id="dcg-ignore-status"></span>
        </div>
        <div id="dcg-ignore-picker" class="dcg-target-picker">
          <p class="dcg-help" style="margin:0">Connect a bot and refresh to list channels.</p>
        </div>
        <textarea class="dcg-textarea" id="channel.ignored_channels" data-field="channel.ignored_channels" style="display:none" aria-hidden="true">${esc(ignoredChannels.join('\n'))}</textarea>
      </div>
      <div class="dcg-section">
        <h4>Allowlisted Bots</h4>
        <div class="dcg-help">Bots she may answer (Bot-to-bot replies must be on). Loaded from servers your bot is in — enable <strong>Server Members Intent</strong> in the Discord Developer Portal if the list is empty.</div>
        <div id="dcg-bot-allowlist-chips" class="dcg-target-chips"><span class="dcg-help">None selected</span></div>
        <div class="dcg-target-toolbar">
          <button type="button" class="dcg-btn" id="dcg-bot-allowlist-refresh">Refresh from Discord</button>
          <button type="button" class="dcg-btn" id="dcg-bot-allowlist-select-all">Select all</button>
          <button type="button" class="dcg-btn" id="dcg-bot-allowlist-clear">Clear all</button>
          <span class="dcg-help" id="dcg-bot-allowlist-status"></span>
        </div>
        <div id="dcg-bot-allowlist-picker" class="dcg-target-picker">
          <p class="dcg-help" style="margin:0">Connect a bot, then click Refresh to load bots.</p>
        </div>
        <textarea class="dcg-textarea" id="bot.allowlist_ids" data-field="bot.allowlist_ids" style="display:none" aria-hidden="true">${esc(allowlistIds.join('\n'))}</textarea>
      </div>
  `;

  const voiceChannelsSection = `
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
  `;

  const voicePromptSection = `
      <div class="dcg-section">
        <h4>Voice Conversation Prompt</h4>
        ${textarea(
          'voice.conversation_prompt_template',
          'Voice conversation prompt',
          values['voice.conversation_prompt_template'] || voicePromptDefault,
          'System instructions for conversational voice mode. Placeholders: {primary} = bot name, {alias_line} = alias suffix (empty when none). Clear and save to restore the built-in default.',
        )}
      </div>
  `;

  const debugSection = `
      <div class="dcg-section">
        <h4>Decision traces</h4>
        <p class="dcg-help">Recent policy / delivery traces — why she replied, stayed quiet, skipped outreach, etc. Filter by type optional.</p>
        <div class="dcg-target-toolbar">
          <select class="dcg-select" id="dcg-traces-filter" style="min-width:180px">
            <option value="">All types</option>
          </select>
          <button type="button" class="dcg-btn" id="dcg-traces-refresh">Refresh now</button>
          <span class="dcg-help" id="dcg-traces-status"></span>
        </div>
        <div id="dcg-traces-summary" class="dcg-help" style="margin:6px 0 10px"></div>
        <div id="dcg-traces-list" class="dcg-debug-list">
          <p class="dcg-help" style="margin:0">Loading…</p>
        </div>
      </div>
      <div class="dcg-section">
        <h4>LLM Debug</h4>
        <p class="dcg-help">Last 10 LLM-related events — successful exchanges and policy rejections. Shows the task's model, the prompt breakdown, and why blocked messages never reached the AI.</p>
        <div class="dcg-target-toolbar">
          <button type="button" class="dcg-btn" id="dcg-debug-refresh">Refresh now</button>
          <button type="button" class="dcg-btn" id="dcg-debug-clear">Clear</button>
          <span class="dcg-help" id="dcg-debug-status"></span>
        </div>
        <div id="dcg-debug-list" class="dcg-debug-list">
          <p class="dcg-help" style="margin:0">Loading…</p>
        </div>
      </div>
  `;

  // Slot order within a tab = array order.
  const slots = [
    { tab: 'Conversation', mount: (el) => { el.innerHTML = allowlistSection; } },
    { tab: 'Voice', mount: (el) => { el.innerHTML = voiceChannelsSection; } },
    { tab: 'Voice', mount: (el) => { el.innerHTML = voicePromptSection; } },
    { tab: 'Debug', mount: (el) => { el.innerHTML = debugSection; } },
  ];

  const settingsBox = container.querySelector('#dcg-schema-form');
  if (!schema.length) {
    settingsBox.insertAdjacentHTML('beforebegin',
      '<div class="dcg-notice">Settings schema unavailable — plugin manifest not loaded. Bot accounts and the widget tabs still work; reload the plugin to restore the settings fields.</div>');
  }
  renderSettingsForm(settingsBox, schema, values, { slots });

  renderAccounts(container, data.accounts?.accounts || []);
  container.querySelector('#dcg-debug').textContent = JSON.stringify({
    health: data.health,
    summary: data.summary,
    trace_summary: data.traces?.trace_summary || {},
    trace_count: (data.traces?.traces || []).length,
  }, null, 2);

  const voicePromptField = fieldByData(container, 'voice.conversation_prompt_template');
  if (voicePromptField && voicePromptDefault) {
    voicePromptField.dataset.defaultTemplate = voicePromptDefault;
  }
  bindAccounts(container);
  initDebugPanel(container, data.llmDebug?.entries || []);
  initTracesPanel(container, data.traces || null);
  initIgnoredChannelPicker(container, ignoredChannels);
  initBotAllowlistPicker(container, allowlistIds);
  initVoiceChannelList(container);
}

const ignoredChannelCatalog = {};
let ignoredChannelSelection = new Set();

// ── LLM debug panel (Debug tab slot) ──

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
    const location = [
      entry.account,
      entry.guild_name || entry.guild_id,
      entry.channel_name || entry.channel_id,
    ].filter(Boolean).join(' · ');
    const latency = timing.latency_ms != null ? `${timing.latency_ms} ms` : '—';
    const modelSummary = formatLlmSummary(entry.llm);

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
          ${esc(location)} · trigger: ${esc(trigger.reason || trigger.username || '—')} · ${esc(modelSummary)} · latency: ${esc(latency)}
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
      status.textContent = data.daemon_running
        ? `Updated ${new Date().toLocaleTimeString()} · daemon running`
        : `Updated ${new Date().toLocaleTimeString()} · daemon offline`;
    }
  } catch (err) {
    if (status) status.textContent = err.message;
    list.innerHTML = `<p class="dcg-help" style="margin:0;color:var(--error)">${esc(err.message)}</p>`;
  }
}

function initDebugPanel(container, initialEntries = []) {
  const list = container.querySelector('#dcg-debug-list');
  const refreshBtn = container.querySelector('#dcg-debug-refresh');
  if (!list) return;
  renderDebugEntries(list, initialEntries);
  refreshBtn?.addEventListener('click', () => refreshDebugPanel(container));
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
  if (_debugRefreshTimer) {
    clearInterval(_debugRefreshTimer);
    _debugRefreshTimer = null;
  }
  _debugRefreshTimer = setInterval(() => {
    if (container.isConnected) refreshDebugPanel(container);
  }, 15000);
}

let _tracesRefreshTimer = null;

function detailPreview(detail) {
  if (!detail || typeof detail !== 'object') return '';
  const bits = [];
  for (const key of ['channel_id', 'reason', 'vibe', 'message_id', 'author_id', 'task_id', 'account_name', 'kind']) {
    if (detail[key] != null && detail[key] !== '') bits.push(`${key}=${detail[key]}`);
    if (bits.length >= 4) break;
  }
  return bits.join(' · ');
}

function renderTracesPanel(listEl, summaryEl, filterEl, data) {
  if (!listEl) return;
  const traces = data?.traces || [];
  const summary = data?.trace_summary || {};
  const byType = summary.by_type || {};
  if (summaryEl) {
    const top = Object.entries(byType)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([k, v]) => `${k}: ${v}`)
      .join(' · ');
    summaryEl.textContent = top
      ? `Last ${summary.total || traces.length} · ${top}`
      : (traces.length ? `${traces.length} traces` : 'No traces yet');
  }
  if (filterEl && filterEl.options.length <= 1) {
    Object.keys(byType).sort().forEach((type) => {
      const opt = document.createElement('option');
      opt.value = type;
      opt.textContent = type;
      filterEl.appendChild(opt);
    });
  }
  if (!traces.length) {
    listEl.innerHTML = '<p class="dcg-help" style="margin:0">No traces yet — chat or wait for cron activity while the daemon runs.</p>';
    return;
  }
  listEl.innerHTML = traces.slice(0, 40).map((t) => {
    const detail = t.detail || {};
    const preview = detailPreview(detail);
    const badgeClass = String(t.trace_type || '').includes('dropped') || String(t.trace_type || '').includes('reject')
      ? 'dcg-debug-badge-warn'
      : (String(t.trace_type || '').includes('sent') || String(t.trace_type || '').includes('emitted')
        ? 'dcg-debug-badge-ok'
        : 'dcg-debug-badge-pending');
    return `
      <details class="dcg-debug-entry">
        <summary><span class="dcg-debug-badge ${badgeClass}">${esc(t.trace_type || 'trace')}</span> ${esc(formatDebugTimestamp(t.created_at))} · ${esc(t.summary || '')}</summary>
        <div class="dcg-debug-meta">${preview ? esc(preview) : '—'}</div>
        <div class="dcg-debug-block">${esc(JSON.stringify(detail, null, 2))}</div>
      </details>`;
  }).join('');
}

async function refreshTracesPanel(container) {
  const list = container.querySelector('#dcg-traces-list');
  const summary = container.querySelector('#dcg-traces-summary');
  const filter = container.querySelector('#dcg-traces-filter');
  const status = container.querySelector('#dcg-traces-status');
  if (!list) return;
  if (status) status.textContent = 'Refreshing…';
  try {
    const type = filter?.value || '';
    const qs = type ? `traces?limit=40&type=${encodeURIComponent(type)}` : 'traces?limit=40';
    const data = await api(qs);
    // Preserve filter selection when rebuilding options from an unfiltered summary once.
    if (filter && !type && (data.trace_summary?.by_type)) {
      const current = filter.value;
      while (filter.options.length > 1) filter.remove(1);
      Object.keys(data.trace_summary.by_type).sort().forEach((t) => {
        const opt = document.createElement('option');
        opt.value = t;
        opt.textContent = t;
        filter.appendChild(opt);
      });
      filter.value = current;
    }
    renderTracesPanel(list, summary, filter, data);
    if (status) {
      status.textContent = data.daemon_running
        ? `Updated ${new Date().toLocaleTimeString()} · daemon running`
        : `Updated ${new Date().toLocaleTimeString()} · daemon offline`;
    }
  } catch (err) {
    if (status) status.textContent = err.message;
    list.innerHTML = `<p class="dcg-help" style="margin:0;color:var(--error)">${esc(err.message)}</p>`;
  }
}

function initTracesPanel(container, initialData = null) {
  const list = container.querySelector('#dcg-traces-list');
  const summary = container.querySelector('#dcg-traces-summary');
  const filter = container.querySelector('#dcg-traces-filter');
  const refreshBtn = container.querySelector('#dcg-traces-refresh');
  if (!list) return;
  if (initialData) renderTracesPanel(list, summary, filter, initialData);
  refreshBtn?.addEventListener('click', () => refreshTracesPanel(container));
  filter?.addEventListener('change', () => refreshTracesPanel(container));
  if (_tracesRefreshTimer) {
    clearInterval(_tracesRefreshTimer);
    _tracesRefreshTimer = null;
  }
  _tracesRefreshTimer = setInterval(() => {
    if (container.isConnected) refreshTracesPanel(container);
  }, 15000);
  if (!initialData) refreshTracesPanel(container);
}

function fieldByData(container, fieldId) {
  return [...container.querySelectorAll('[data-field]')].find((el) => el.dataset.field === fieldId) || null;
}

function checkboxByDataValue(container, pickerId, value) {
  return [...container.querySelectorAll(`#${pickerId} input[type="checkbox"]`)]
    .find((el) => el.dataset.value === value) || null;
}

function ignoredChannelLabel(value) {
  return ignoredChannelCatalog[value]?.label || value;
}

function syncIgnoredChannelsField(container) {
  const hidden = fieldByData(container, 'channel.ignored_channels');
  if (hidden) {
    hidden.value = [...ignoredChannelSelection].sort().join('\n');
  }
}

function renderIgnoredChannelChips(container) {
  const box = container.querySelector('#dcg-ignore-chips');
  if (!box) return;
  box.innerHTML = '';
  if (!ignoredChannelSelection.size) {
    box.innerHTML = '<span class="dcg-help">None selected</span>';
    return;
  }
  [...ignoredChannelSelection].sort().forEach((value) => {
    const chip = document.createElement('span');
    chip.className = 'dcg-target-chip';
    const text = document.createElement('span');
    text.textContent = ignoredChannelLabel(value);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Remove');
    btn.textContent = '×';
    btn.addEventListener('click', () => {
      ignoredChannelSelection.delete(value);
      const cb = checkboxByDataValue(container, 'dcg-ignore-picker', value);
      if (cb) cb.checked = false;
      renderIgnoredChannelChips(container);
      syncIgnoredChannelsField(container);
    });
    chip.appendChild(text);
    chip.appendChild(btn);
    box.appendChild(chip);
  });
}

function renderIgnoredChannelPicker(container, targets) {
  const box = container.querySelector('#dcg-ignore-picker');
  if (!box) return;
  box.innerHTML = '';
  if (!targets.length) {
    box.innerHTML = '<p class="dcg-help" style="margin:0">No text channels found. Connect a bot and ensure the daemon is running.</p>';
    return;
  }
  const groups = {};
  targets.forEach((target) => {
    ignoredChannelCatalog[target.value] = target;
    const key = `${target.account}|${target.guild_id}`;
    if (!groups[key]) {
      groups[key] = { title: `${target.account} · ${target.guild_name}`, items: [] };
    }
    groups[key].items.push(target);
  });
  Object.values(groups).forEach((group) => {
    const groupEl = document.createElement('div');
    groupEl.className = 'dcg-target-group';
    const title = document.createElement('div');
    title.className = 'dcg-target-group-title';
    title.textContent = group.title;
    groupEl.appendChild(title);
    group.items.forEach((target) => {
      const label = document.createElement('label');
      label.className = 'dcg-target-option';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.dataset.value = target.value;
      cb.checked = ignoredChannelSelection.has(target.value);
      cb.addEventListener('change', () => {
        if (cb.checked) ignoredChannelSelection.add(target.value);
        else ignoredChannelSelection.delete(target.value);
        renderIgnoredChannelChips(container);
        syncIgnoredChannelsField(container);
      });
      const span = document.createElement('span');
      span.textContent = `#${target.channel_name}`;
      label.appendChild(cb);
      label.appendChild(span);
      groupEl.appendChild(label);
    });
    box.appendChild(groupEl);
  });
}

async function loadIgnoredChannelPicker(container) {
  const status = container.querySelector('#dcg-ignore-status');
  const refreshBtn = container.querySelector('#dcg-ignore-refresh');
  if (refreshBtn) refreshBtn.disabled = true;
  if (status) status.textContent = 'Loading…';
  try {
    const data = await api('channels/text');
    if (data.error) throw new Error(data.error);
    renderIgnoredChannelPicker(container, data.targets || []);
    if (status) {
      status.textContent = data.connected
        ? `${(data.targets || []).length} channels`
        : 'Daemon offline / no bots connected';
    }
  } catch (err) {
    if (status) status.textContent = err.message || 'Failed to load channels';
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

function initIgnoredChannelPicker(container, selectedTargets) {
  ignoredChannelSelection = new Set((selectedTargets || []).map((line) => String(line).trim()).filter(Boolean));
  syncIgnoredChannelsField(container);
  renderIgnoredChannelChips(container);

  container.querySelector('#dcg-ignore-refresh')?.addEventListener('click', () => loadIgnoredChannelPicker(container));
  container.querySelector('#dcg-ignore-clear')?.addEventListener('click', () => {
    ignoredChannelSelection.clear();
    container.querySelectorAll('#dcg-ignore-picker input[type="checkbox"]').forEach((cb) => {
      cb.checked = false;
    });
    renderIgnoredChannelChips(container);
    syncIgnoredChannelsField(container);
  });

  if (container.querySelector('.dcg-notice')?.textContent?.includes('Daemon is running')) {
    loadIgnoredChannelPicker(container);
  }
}

const botAllowlistCatalog = {};
let botAllowlistSelection = new Set();

function botAllowlistLabel(value) {
  return botAllowlistCatalog[value]?.label || value;
}

function syncBotAllowlistField(container) {
  const hidden = fieldByData(container, 'bot.allowlist_ids');
  if (hidden) {
    hidden.value = [...botAllowlistSelection].sort().join('\n');
  }
}

function renderBotAllowlistChips(container) {
  const box = container.querySelector('#dcg-bot-allowlist-chips');
  if (!box) return;
  box.innerHTML = '';
  if (!botAllowlistSelection.size) {
    box.innerHTML = '<span class="dcg-help">None selected</span>';
    return;
  }
  [...botAllowlistSelection].sort().forEach((value) => {
    const chip = document.createElement('span');
    chip.className = 'dcg-target-chip';
    const text = document.createElement('span');
    text.textContent = botAllowlistLabel(value);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Remove');
    btn.textContent = '×';
    btn.addEventListener('click', () => {
      botAllowlistSelection.delete(value);
      const cb = checkboxByDataValue(container, 'dcg-bot-allowlist-picker', value);
      if (cb) cb.checked = false;
      renderBotAllowlistChips(container);
      syncBotAllowlistField(container);
    });
    chip.appendChild(text);
    chip.appendChild(btn);
    box.appendChild(chip);
  });
}

function renderBotAllowlistPicker(container, bots) {
  const box = container.querySelector('#dcg-bot-allowlist-picker');
  if (!box) return;
  box.innerHTML = '';
  if (!bots.length) {
    box.innerHTML = '<p class="dcg-help" style="margin:0">No other bots found in connected servers. Bots must share a server with Remmi and appear in the member list.</p>';
    return;
  }
  const groupEl = document.createElement('div');
  groupEl.className = 'dcg-target-group';
  const title = document.createElement('div');
  title.className = 'dcg-target-group-title';
  title.textContent = 'Bots in your servers';
  groupEl.appendChild(title);
  bots.forEach((bot) => {
    botAllowlistCatalog[bot.value] = bot;
    const label = document.createElement('label');
    label.className = 'dcg-target-option';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.dataset.value = bot.value;
    cb.checked = botAllowlistSelection.has(bot.value);
    cb.addEventListener('change', () => {
      if (cb.checked) botAllowlistSelection.add(bot.value);
      else botAllowlistSelection.delete(bot.value);
      renderBotAllowlistChips(container);
      syncBotAllowlistField(container);
    });
    const span = document.createElement('span');
    span.textContent = bot.label;
    label.appendChild(cb);
    label.appendChild(span);
    groupEl.appendChild(label);
  });
  box.appendChild(groupEl);
}

async function loadBotAllowlistPicker(container) {
  const status = container.querySelector('#dcg-bot-allowlist-status');
  const refreshBtn = container.querySelector('#dcg-bot-allowlist-refresh');
  if (refreshBtn) refreshBtn.disabled = true;
  if (status) status.textContent = 'Loading…';
  try {
    const data = await api('bots/allowlist');
    if (data.error && !data.bots?.length) throw new Error(data.error);
    renderBotAllowlistPicker(container, data.bots || []);
    const count = (data.bots || []).length;
    if (status) {
      status.textContent = count
        ? `${count} bot${count === 1 ? '' : 's'} found`
        : (data.error || 'No connected bots');
    }
    renderBotAllowlistChips(container);
  } catch (err) {
    if (status) status.textContent = err.message || 'Failed to load bots';
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

function initBotAllowlistPicker(container, selectedIds) {
  botAllowlistSelection = new Set((selectedIds || []).map((line) => String(line).trim()).filter(Boolean));
  syncBotAllowlistField(container);
  renderBotAllowlistChips(container);

  container.querySelector('#dcg-bot-allowlist-refresh')?.addEventListener('click', () => loadBotAllowlistPicker(container));
  container.querySelector('#dcg-bot-allowlist-select-all')?.addEventListener('click', () => {
    container.querySelectorAll('#dcg-bot-allowlist-picker input[type="checkbox"]').forEach((cb) => {
      cb.checked = true;
      botAllowlistSelection.add(cb.dataset.value);
    });
    renderBotAllowlistChips(container);
    syncBotAllowlistField(container);
  });
  container.querySelector('#dcg-bot-allowlist-clear')?.addEventListener('click', () => {
    botAllowlistSelection.clear();
    container.querySelectorAll('#dcg-bot-allowlist-picker input[type="checkbox"]').forEach((cb) => {
      cb.checked = false;
    });
    renderBotAllowlistChips(container);
    syncBotAllowlistField(container);
  });

  if (container.querySelector('.dcg-notice')?.textContent?.includes('Daemon is running')) {
    loadBotAllowlistPicker(container);
  }
}

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

function initVoiceChannelList(container) {
  container.querySelector('#dcg-voice-channels-refresh')?.addEventListener('click', () => loadVoiceChannelList(container));
  if (container.querySelector('.dcg-notice')?.textContent?.includes('Daemon is running')) {
    loadVoiceChannelList(container);
  }
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
  const flat = {
    'channel.ignored_channels': [...ignoredChannelSelection].sort(),
    'bot.allowlist_ids': [...botAllowlistSelection].sort(),
    'voice.conversation_prompt_template': normalizedVoicePrompt(container),
  };
  return flat;
}

async function loadPanelData() {
  const [accounts, settings, health, summary, traces, llmDebug, plugins, values] = await Promise.allSettled([
    api('accounts'),
    api('settings'), // daemon state + built-in defaults only — values live in core now
    api('health'),
    api('admin/summary'),
    api('traces'),
    api('debug/llm?limit=10'),
    pluginsAPI.listPlugins(),
    pluginsAPI.getSettings(PLUGIN_NAME),
  ]);
  const val = (r, fallback = {}) => (r.status === 'fulfilled' ? r.value : fallback);
  const settingsData = val(settings, {});
  const healthData = val(health, {});
  const daemonRunning = settingsData.daemon_running === true
    || healthData.daemon_running === true
    || healthData.state === 'ready'
    || healthData.state === 'starting';
  _SCHEMA = (val(plugins, {}).plugins || []).find((p) => p.name === PLUGIN_NAME)?.settings_schema || [];
  return {
    accounts: val(accounts, { accounts: [] }),
    schema: _SCHEMA,
    values: val(values, {}),
    settings: {
      defaults: settingsData.defaults || {},
      daemon_running: daemonRunning,
      daemon_state: settingsData.daemon_state || healthData.state || 'unknown',
    },
    health: healthData,
    summary: val(summary, {}),
    traces: val(traces, { traces: [] }),
    llmDebug: val(llmDebug, { entries: [] }),
  };
}

function registerTab() {
  registerPluginSettings({
    id: PLUGIN_NAME,
    name: 'Discord',
    icon: '🎮',
    helpText: 'Discord bot accounts, conversation behavior, reactions, safety, media, and voice settings. Greetings live on the Discord: Greetings / All interactions daemon tasks.',

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
      // Wipe guard: customSectionValues returns plausible EMPTIES (auto
      // providers, empty target lists) when the slot sections aren't in the
      // DOM — saving that would silently wipe 15 stored keys. If the panel
      // hasn't finished rendering (save clicked mid-load, load failed, slot
      // mount threw), refuse; the settings page surfaces this as
      // "Save failed: …" instead of a lying success toast.
      const box = container.querySelector('#dcg-schema-form');
      if (!box) {
        throw new Error('Discord panel is still loading — wait a moment and save again.');
      }
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
  if (name === PLUGIN_NAME && detail.enabled) {
    registerTab();
  }
});

export default { init() { registerTab(); } };
