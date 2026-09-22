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

function llmProviderBlockHtml({
  prefix,
  label,
  help,
  providerField,
  modelField,
  providerValue = 'auto',
  modelValue = '',
  autoLabel = 'Auto (continuity task)',
}) {
  const provider = providerValue || 'auto';
  return `
    <div class="dcg-row">
      <div>
        <label for="${prefix}-primary">${esc(label)}</label>
        ${help ? `<div class="dcg-help">${help}</div>` : ''}
      </div>
      <select class="dcg-select" id="${prefix}-primary" data-field="${providerField}">
        <option value="auto" ${provider === 'auto' ? 'selected' : ''}>${esc(autoLabel)}</option>
        ${provider !== 'auto' ? `<option value="${esc(provider)}" selected>${esc(provider)}</option>` : ''}
      </select>
    </div>
    <div class="dcg-row" id="${prefix}-model-row" style="display:none">
      <div>
        <label for="${prefix}-model-select">Model</label>
        <div class="dcg-help">Leave default to use the provider's configured model.</div>
      </div>
      <select class="dcg-select" id="${prefix}-model-select"></select>
    </div>
    <div class="dcg-row" id="${prefix}-model-custom-row" style="display:none">
      <div><label for="${prefix}-model-custom">Model name</label></div>
      <input class="dcg-input" id="${prefix}-model-custom" type="text" placeholder="model id">
    </div>
    <input type="hidden" id="${prefix}-model-field" data-field="${modelField}" value="${esc(modelValue || '')}">
  `;
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

  const replyProvider = values['cognitive.llm_primary'] || 'auto';
  const replyModel = values['cognitive.llm_model'] || '';
  const voiceProvider = values['voice.llm_provider'] || '';
  const voiceModel = values['voice.llm_model'] || '';
  const visionProvider = values['media.vision_llm_provider'] || '';
  const visionModel = values['media.vision_llm_model'] || '';
  const ignoredChannels = values['channel.ignored_channels'] || [];
  const allowlistIds = values['bot.allowlist_ids'] || [];
  const joinTargets = values['voice.join_targets'] || [];
  const sentimentBackend = values['reaction.sentiment_backend'] || 'vader';

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
  const modelsSection = `
      <div class="dcg-section">
        <h4>Language Models</h4>
        ${llmProviderBlockHtml({
          prefix: 'dcg-llm',
          label: 'Reply LLM',
          help: 'Override for Discord text and voice replies. Daemon chooses (default) = the model the daemon task already uses.',
          providerField: 'cognitive.llm_primary',
          modelField: 'cognitive.llm_model',
          providerValue: replyProvider,
          modelValue: replyModel,
          autoLabel: 'Daemon chooses (default)',
        })}
        ${llmProviderBlockHtml({
          prefix: 'dcg-voice-llm',
          label: 'Voice LLM',
          help: 'Override for live voice-channel replies. A fast non-thinking model keeps turns snappy. Default leaves the voice chat on its own settings. Takes effect on next /voice join.',
          providerField: 'voice.llm_provider',
          modelField: 'voice.llm_model',
          providerValue: voiceProvider || 'auto',
          modelValue: voiceModel,
          autoLabel: 'Voice chat default',
        })}
      </div>
  `;

  const visionLlmSection = `
      <div class="dcg-section">
        <h4>Vision LLM</h4>
        ${llmProviderBlockHtml({
          prefix: 'dcg-vision-llm',
          label: 'Vision LLM',
          help: 'Override for describing images posted in chat — same credentials as your registered providers, no separate vision key. Daemon chooses (default) = the same model her replies use; if that model can\'t see images, captions fall back to filenames rather than calling a provider you didn\'t pick.',
          providerField: 'media.vision_llm_provider',
          modelField: 'media.vision_llm_model',
          providerValue: visionProvider || 'auto',
          modelValue: visionModel,
          autoLabel: 'Daemon chooses (default)',
        })}
      </div>
  `;

  const socialSentimentSection = `
      <div class="dcg-section">
        <h4>Reaction sentiment</h4>
        <p class="dcg-help">Chooses emoji for autonomous reactions from message tone. VADER is lightweight and loads instantly. Twitter RoBERTa uses more CPU and memory (downloads a transformer model on first use) but is more accurate on informal chat, slang, and emoji-heavy messages.</p>
        <div class="dcg-row">
          <div>
            <label for="dcg-sentiment-backend">Sentiment engine</label>
          </div>
          <select class="dcg-select" id="dcg-sentiment-backend" data-field="reaction.sentiment_backend">
            <option value="vader" ${sentimentBackend === 'vader' ? 'selected' : ''}>VADER (lightweight)</option>
            <option value="twitter_roberta" ${sentimentBackend === 'twitter_roberta' ? 'selected' : ''}>Twitter RoBERTa (more accurate)</option>
          </select>
        </div>
        <div class="dcg-help" style="margin-top:4px">Twitter RoBERTa requires <code>pip install transformers torch</code>. If missing, reactions fall back to keyword heuristics.</div>
      </div>
  `;

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
        <div class="dcg-help">Bots Remmi may debate or interact with (see Conversation → bot reply mode). Loaded from servers your bot is in — enable <strong>Server Members Intent</strong> in the Discord Developer Portal if the list is empty.</div>
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

  const voiceJoinSection = `
      <div class="dcg-section">
        <h4>Auto-Join Voice Channels</h4>
        <div class="dcg-help">Voice channels to auto-join when someone is in them, and leave when empty. Polled every ~15s while the daemon runs.</div>
        <div id="dcg-voice-target-chips" class="dcg-target-chips"><span class="dcg-help">None selected</span></div>
        <div class="dcg-target-toolbar">
          <button type="button" class="dcg-btn" id="dcg-voice-target-refresh">Refresh from Discord</button>
          <button type="button" class="dcg-btn" id="dcg-voice-target-select-all">Select all</button>
          <button type="button" class="dcg-btn" id="dcg-voice-target-clear">Clear all</button>
          <span class="dcg-help" id="dcg-voice-target-status"></span>
        </div>
        <div id="dcg-voice-target-picker" class="dcg-target-picker">
          <p class="dcg-help" style="margin:0">Connect a bot, then click Refresh to load voice channels.</p>
        </div>
        <textarea class="dcg-textarea" id="voice.join_targets" data-field="voice.join_targets" style="display:none" aria-hidden="true">${esc(joinTargets.join('\n'))}</textarea>
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
        <p class="dcg-help">Last 10 LLM-related events — successful exchanges and policy rejections. Shows configured vs resolved model, prompt breakdown, delivery edits, and why blocked messages never reached the AI.</p>
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

  // Slot order within a tab = array order; a tab named here that no manifest
  // field uses (Models) is created by the renderer.
  const slots = [
    { tab: 'Models', mount: (el) => { el.innerHTML = modelsSection; } },
    { tab: 'Conversation', mount: (el) => { el.innerHTML = allowlistSection; } },
    { tab: 'Social', mount: (el) => { el.innerHTML = socialSentimentSection; } },
    { tab: 'Voice', mount: (el) => { el.innerHTML = voiceJoinSection; } },
    { tab: 'Voice', mount: (el) => { el.innerHTML = voicePromptSection; } },
    { tab: 'Media', mount: (el) => { el.innerHTML = visionLlmSection; } },
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
  initVoiceTargetPicker(container, joinTargets);
  initLlmProviderBlocks(container, {
    reply: {
      prefix: 'dcg-llm',
      providerKey: replyProvider,
      modelName: replyModel,
      autoLabel: 'Daemon chooses (default)',
    },
    voice: {
      prefix: 'dcg-voice-llm',
      providerKey: voiceProvider || 'auto',
      modelName: voiceModel,
      autoLabel: 'Voice chat default',
    },
    vision: {
      prefix: 'dcg-vision-llm',
      providerKey: visionProvider || 'auto',
      modelName: visionModel,
      autoLabel: 'Daemon chooses (default)',
    },
  });
}

let _LLM_PROVIDERS = [];
let _LLM_METADATA = {};

async function loadLlmProviders() {
  const response = await fetch('/api/llm/providers');
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const data = await response.json();
  _LLM_PROVIDERS = data.providers || [];
  _LLM_METADATA = data.metadata || {};
  return data;
}

function llmProviderOptionsHtml(selected = 'auto', autoLabel = 'Auto (continuity task)') {
  const options = [`<option value="auto" ${selected === 'auto' ? 'selected' : ''}>${esc(autoLabel)}</option>`];
  for (const provider of _LLM_PROVIDERS) {
    const key = provider.key || provider.display_name || '';
    if (!key) continue;
    // A disabled provider pinned here makes EVERY reply die silently
    // (explicit pins have no fallback). Mark it and block fresh selection;
    // the stored value stays selectable so an existing pin remains visible.
    const off = provider.enabled === false;
    const label = (provider.display_name || key) + (off ? ' — DISABLED in LLM settings' : '');
    const disabledAttr = off && key !== selected ? ' disabled' : '';
    options.push(`<option value="${esc(key)}" ${key === selected ? 'selected' : ''}${disabledAttr}>${esc(label)}</option>`);
  }
  return options.join('');
}

function llmBlockElements(container, prefix) {
  return {
    primary: container.querySelector(`#${prefix}-primary`),
    modelRow: container.querySelector(`#${prefix}-model-row`),
    modelCustomRow: container.querySelector(`#${prefix}-model-custom-row`),
    modelSelect: container.querySelector(`#${prefix}-model-select`),
    modelCustom: container.querySelector(`#${prefix}-model-custom`),
    modelField: container.querySelector(`#${prefix}-model-field`),
  };
}

function readLlmBlockValues(container, prefix) {
  const els = llmBlockElements(container, prefix);
  if (!els.primary || !els.modelField) {
    return { provider: 'auto', model: '' };
  }
  syncLlmModelField(container, prefix);
  return {
    provider: els.primary.value || 'auto',
    model: els.modelField.value || '',
  };
}

function updateLlmModelSelector(container, prefix, providerKey, currentModel) {
  const els = llmBlockElements(container, prefix);
  if (!els.modelField) return;

  if (els.modelRow) els.modelRow.style.display = 'none';
  if (els.modelCustomRow) els.modelCustomRow.style.display = 'none';

  if (!providerKey || providerKey === 'auto') {
    els.modelField.value = '';
    return;
  }

  const meta = _LLM_METADATA[providerKey];
  const conf = _LLM_PROVIDERS.find((item) => item.key === providerKey);
  const modelOptions = meta?.model_options || {};
  const optionKeys = Object.keys(modelOptions);

  if (optionKeys.length > 0 && els.modelSelect && els.modelRow) {
    const defaultModel = conf?.model || '';
    const defaultLabel = defaultModel
      ? `Default (${modelOptions[defaultModel] || defaultModel})`
      : 'Default';
    let html = `<option value="">${esc(defaultLabel)}</option>`;
    html += optionKeys.map((key) => (
      `<option value="${esc(key)}" ${key === currentModel ? 'selected' : ''}>${esc(modelOptions[key])}</option>`
    )).join('');
    if (currentModel && !modelOptions[currentModel]) {
      html += `<option value="${esc(currentModel)}" selected>${esc(currentModel)}</option>`;
    }
    els.modelSelect.innerHTML = html;
    els.modelField.value = currentModel || '';
    els.modelRow.style.display = '';
    return;
  }

  if (els.modelCustom && els.modelCustomRow) {
    els.modelCustom.value = currentModel || '';
    els.modelField.value = currentModel || '';
    els.modelCustomRow.style.display = '';
  }
}

function syncLlmModelField(container, prefix) {
  const els = llmBlockElements(container, prefix);
  if (!els.primary || !els.modelField) return;
  const provider = els.primary.value || 'auto';
  if (provider === 'auto') {
    els.modelField.value = '';
    return;
  }
  if (els.modelRow && els.modelRow.style.display !== 'none' && els.modelSelect) {
    els.modelField.value = els.modelSelect.value || '';
    return;
  }
  if (els.modelCustom) {
    els.modelField.value = (els.modelCustom.value || '').trim();
  }
}

function initLlmProviderBlock(container, { prefix, providerKey, modelName, onProviderChange, autoLabel }) {
  const els = llmBlockElements(container, prefix);
  if (!els.primary) return;

  const applyProvider = (key, model) => {
    els.primary.innerHTML = llmProviderOptionsHtml(key || 'auto', autoLabel || undefined);
    if (key && key !== 'auto') {
      els.primary.value = key;
      // Provider list unavailable (fetch failed) — keep the STORED key as a
      // real option instead of letting a blank select read back as 'auto'
      // and reset the pin on the next save (scout finding, 2026-08-05).
      if (els.primary.value !== key) {
        els.primary.insertAdjacentHTML('beforeend', `<option value="${esc(key)}" selected>${esc(key)}</option>`);
        els.primary.value = key;
      }
    }
    updateLlmModelSelector(container, prefix, els.primary.value, model || '');
  };

  loadLlmProviders()
    .then(() => applyProvider(providerKey || 'auto', modelName || ''))
    .catch((err) => {
      console.warn(`[discord_cognitive] LLM provider list unavailable for ${prefix}:`, err);
      applyProvider(providerKey || 'auto', modelName || '');
    });

  els.primary.addEventListener('change', () => {
    if (onProviderChange) onProviderChange();
    updateLlmModelSelector(container, prefix, els.primary.value, '');
  });

  els.modelSelect?.addEventListener('change', (event) => {
    if (els.modelField) els.modelField.value = event.target.value || '';
    if (onProviderChange) onProviderChange();
  });

  els.modelCustom?.addEventListener('input', (event) => {
    if (els.modelField) els.modelField.value = (event.target.value || '').trim();
    if (onProviderChange) onProviderChange();
  });
}

function initLlmProviderBlocks(container, blocks) {
  initLlmProviderBlock(container, { ...blocks.reply });
  if (blocks.voice) {
    initLlmProviderBlock(container, { ...blocks.voice });
  }
  if (blocks.vision) {
    initLlmProviderBlock(container, { ...blocks.vision });
  }
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

function formatLlmSummary(llm, prompt) {
  const resolved = llm || {};
  const configured = [resolved.configured_primary, resolved.configured_model].filter(Boolean).join(' / ') || 'auto';
  const resolvedLabel = [resolved.resolved_primary, resolved.resolved_model].filter(Boolean).join(' / ') || configured;
  const eventOverride = resolved.event_primary
    ? ` · event override: ${resolved.event_primary}${resolved.event_model ? ` / ${resolved.event_model}` : ''}`
    : '';
  const taskLabel = resolved.task_name ? ` · task: ${resolved.task_name}` : '';
  if (resolvedLabel !== configured && configured !== 'auto') {
    return `configured ${configured} → resolved ${resolvedLabel}${eventOverride}${taskLabel}`;
  }
  if (resolvedLabel !== 'auto') {
    return `resolved ${resolvedLabel}${eventOverride}${taskLabel}`;
  }
  const fallback = [prompt?.llm_primary, prompt?.llm_model].filter(Boolean).join(' / ');
  return fallback ? `configured ${fallback}${taskLabel}` : `auto${taskLabel}`;
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
    const modelSummary = formatLlmSummary(entry.llm, prompt);

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

    const typoLine = delivery.typo_applied
      ? `Typo sent → corrected after ${Number(delivery.edit_delay_seconds || 0).toFixed(1)}s (${esc(delivery.edit_kind || 'edit')})`
      : 'No human-like typo edit';
    const sentText = delivery.sent_text || (response.parsed_chunks || [])[0] || '';
    const correctedText = delivery.corrected_text || sentText;
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
        <div class="dcg-debug-block">${esc(typoLine)}
${delivery.typo_applied ? `\nSent: ${esc(sentText)}\nCorrected: ${esc(correctedText)}` : `\nSent: ${esc(sentText)}`}
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

const voiceTargetCatalog = {};
let voiceTargetSelection = new Set();

function voiceTargetLabel(value) {
  return voiceTargetCatalog[value]?.label || value;
}

function syncVoiceTargetsField(container) {
  const hidden = fieldByData(container, 'voice.join_targets');
  if (hidden) {
    hidden.value = [...voiceTargetSelection].sort().join('\n');
  }
}

function renderVoiceTargetChips(container) {
  const box = container.querySelector('#dcg-voice-target-chips');
  if (!box) return;
  box.innerHTML = '';
  if (!voiceTargetSelection.size) {
    box.innerHTML = '<span class="dcg-help">None selected</span>';
    return;
  }
  [...voiceTargetSelection].sort().forEach((value) => {
    const chip = document.createElement('span');
    chip.className = 'dcg-target-chip';
    const text = document.createElement('span');
    text.textContent = voiceTargetLabel(value);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Remove');
    btn.textContent = '×';
    btn.addEventListener('click', () => {
      voiceTargetSelection.delete(value);
      const cb = checkboxByDataValue(container, 'dcg-voice-target-picker', value);
      if (cb) cb.checked = false;
      renderVoiceTargetChips(container);
      syncVoiceTargetsField(container);
    });
    chip.appendChild(text);
    chip.appendChild(btn);
    box.appendChild(chip);
  });
}

function renderVoiceTargetPicker(container, targets) {
  const box = container.querySelector('#dcg-voice-target-picker');
  if (!box) return;
  box.innerHTML = '';
  if (!targets.length) {
    box.innerHTML = '<p class="dcg-help" style="margin:0">No voice channels found. Connect a bot and ensure the daemon is running.</p>';
    return;
  }
  const groups = {};
  targets.forEach((target) => {
    voiceTargetCatalog[target.value] = target;
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
      cb.checked = voiceTargetSelection.has(target.value);
      cb.addEventListener('change', () => {
        if (cb.checked) voiceTargetSelection.add(target.value);
        else voiceTargetSelection.delete(target.value);
        renderVoiceTargetChips(container);
        syncVoiceTargetsField(container);
      });
      const span = document.createElement('span');
      const count = Number(target.member_count || 0);
      span.textContent = count ? `${target.channel_name} (${count} in channel)` : target.channel_name;
      label.appendChild(cb);
      label.appendChild(span);
      groupEl.appendChild(label);
    });
    box.appendChild(groupEl);
  });
}

async function loadVoiceTargetPicker(container) {
  const status = container.querySelector('#dcg-voice-target-status');
  const refreshBtn = container.querySelector('#dcg-voice-target-refresh');
  if (refreshBtn) refreshBtn.disabled = true;
  if (status) status.textContent = 'Loading…';
  try {
    let data;
    try {
      data = await api('voice/targets');
    } catch (err) {
      if (!/404|not found/i.test(String(err.message))) throw err;
      data = await api('channels/text?channel_type=voice');
    }
    if (data.error && !data.targets?.length) throw new Error(data.error);
    renderVoiceTargetPicker(container, data.targets || []);
    const count = (data.targets || []).length;
    if (status) {
      status.textContent = count
        ? `${count} voice channel${count === 1 ? '' : 's'} available`
        : (data.error || 'No connected bots');
    }
    renderVoiceTargetChips(container);
  } catch (err) {
    if (status) status.textContent = err.message || 'Failed to load voice channels';
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

function initVoiceTargetPicker(container, selectedTargets) {
  voiceTargetSelection = new Set((selectedTargets || []).map((line) => String(line).trim()).filter(Boolean));
  syncVoiceTargetsField(container);
  renderVoiceTargetChips(container);

  container.querySelector('#dcg-voice-target-refresh')?.addEventListener('click', () => loadVoiceTargetPicker(container));
  container.querySelector('#dcg-voice-target-select-all')?.addEventListener('click', () => {
    container.querySelectorAll('#dcg-voice-target-picker input[type="checkbox"]').forEach((cb) => {
      cb.checked = true;
      voiceTargetSelection.add(cb.dataset.value);
    });
    renderVoiceTargetChips(container);
    syncVoiceTargetsField(container);
  });
  container.querySelector('#dcg-voice-target-clear')?.addEventListener('click', () => {
    voiceTargetSelection.clear();
    container.querySelectorAll('#dcg-voice-target-picker input[type="checkbox"]').forEach((cb) => {
      cb.checked = false;
    });
    renderVoiceTargetChips(container);
    syncVoiceTargetsField(container);
  });

  if (container.querySelector('.dcg-notice')?.textContent?.includes('Daemon is running')) {
    loadVoiceTargetPicker(container);
  }
}

// Voice prompt keeps the '' sentinel convention: matching the built-in default
// stores blank so the plugin default can evolve without stale copies.
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
  const reply = readLlmBlockValues(container, 'dcg-llm');
  // Voice block was missing here — voice.llm_provider/llm_model never saved
  // from the UI (found during the 2026-08-05 slot conversion). ensure_voice_chat
  // treats ''/'auto' as hands-off, so persisting 'auto' is safe.
  const voice = readLlmBlockValues(container, 'dcg-voice-llm');
  const vision = readLlmBlockValues(container, 'dcg-vision-llm');
  const flat = {
    'cognitive.llm_primary': reply.provider,
    'cognitive.llm_model': reply.model,
    'voice.llm_provider': voice.provider,
    'voice.llm_model': voice.model,
    'media.vision_llm_provider': vision.provider,
    'media.vision_llm_model': vision.model,
    'reaction.sentiment_backend': container.querySelector('#dcg-sentiment-backend')?.value || 'vader',
    'channel.ignored_channels': [...ignoredChannelSelection].sort(),
    'bot.allowlist_ids': [...botAllowlistSelection].sort(),
    'voice.join_targets': [...voiceTargetSelection].sort(),
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
    helpText: 'Discord bot accounts, conversation behavior, social reactions, delivery quirks, safety, media, and voice settings. Greetings live on the Discord: Greetings / All interactions daemon tasks.',

    load: () => loadPanelData(),

    render(container, data) {
      try {
        renderShell(container, data || {});
      } catch (err) {
        console.error('[discord_cognitive] settings render failed:', err);
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
      // Guard EVERY slot customSectionValues reads, not just the reply LLM —
      // a failed Media/Voice slot mount returns fabricated
      // {provider:'auto'} values that would overwrite explicit pins on save.
      // Residual of the 2026-08-05 wipe-guard class.
      const slotPrefixes = ['dcg-llm', 'dcg-voice-llm', 'dcg-vision-llm'];
      const missing = slotPrefixes.filter((p) => !container.querySelector(`#${p}-primary`));
      if (!box || missing.length) {
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
