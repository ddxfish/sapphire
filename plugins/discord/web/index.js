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
  return d.innerHTML;
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
  const greetingProvider = values['proactive.greeting_model_provider'] || '';
  const greetingModel = values['proactive.greeting_model_name'] || '';
  const goodnightProvider = values['proactive.goodnight_model_provider'] || '';
  const goodnightModel = values['proactive.goodnight_model_name'] || '';
  const distillProvider = values['profile.distill_model_provider'] || '';
  const distillModel = values['profile.distill_model_name'] || '';
  const voiceProvider = values['voice.llm_provider'] || '';
  const voiceModel = values['voice.llm_model'] || '';
  const visionProvider = values['media.vision_llm_provider'] || '';
  const visionModel = values['media.vision_llm_model'] || '';
  const greetingTargets = values['proactive.greeting_targets'] || [];
  const ignoredChannels = values['channel.ignored_channels'] || [];
  const allowlistIds = values['bot.allowlist_ids'] || [];
  const joinTargets = values['voice.join_targets'] || [];
  const activityPresets = values['presence.activity_presets'] || [];
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
          prefix: 'dcg-greeting-llm',
          label: 'Greeting model provider',
          help: 'LLM for morning greetings. Defaults to Reply LLM when unset.',
          providerField: 'proactive.greeting_model_provider',
          modelField: 'proactive.greeting_model_name',
          providerValue: greetingProvider || replyProvider,
          modelValue: greetingModel || replyModel,
        })}
        ${llmProviderBlockHtml({
          prefix: 'dcg-goodnight-llm',
          label: 'Goodnight model provider',
          help: 'LLM for goodnight messages. Defaults to Greeting provider, then Reply LLM.',
          providerField: 'proactive.goodnight_model_provider',
          modelField: 'proactive.goodnight_model_name',
          providerValue: goodnightProvider || greetingProvider || replyProvider,
          modelValue: goodnightModel || greetingModel || replyModel,
        })}
        ${llmProviderBlockHtml({
          prefix: 'dcg-distill-llm',
          label: 'Ambient distill LLM',
          help: 'LLM used when Memory → Learn from ambient chat is on. Extracts durable personal facts into the plugin DB. Defaults to Reply LLM when unset.',
          providerField: 'profile.distill_model_provider',
          modelField: 'profile.distill_model_name',
          providerValue: distillProvider || replyProvider,
          modelValue: distillModel || replyModel,
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

  const memorySection = `
      <div class="dcg-section">
        <h4>People She Knows</h4>
        <div class="dcg-help">Per-user memory for the selected bot — names, facts, interests, relationship milestones. Stored in the plugin's own database, never in her core Mind. Soft-forget hides one fact; Forget permanently deletes everything about that user.</div>
        <div class="dcg-target-toolbar">
          <select class="dcg-select" id="dcg-mem-account"></select>
          <button type="button" class="dcg-btn" id="dcg-mem-refresh">Refresh</button>
          <span class="dcg-help" id="dcg-mem-status"></span>
        </div>
        <div id="dcg-mem-list" class="dcg-target-picker">
          <p class="dcg-help" style="margin:0">Loading…</p>
        </div>
      </div>
      <div class="dcg-section">
        <h4>Ambient distill review</h4>
        <div class="dcg-help">Unpinned facts learned from ambient chat (source <code>ambient_distill</code>). <strong>Pin</strong> keeps them; <strong>Soft-forget</strong> hides junk without wiping the person.</div>
        <div class="dcg-target-toolbar">
          <button type="button" class="dcg-btn" id="dcg-distill-review-refresh">Refresh queue</button>
          <label class="dcg-help" style="display:inline-flex;gap:6px;align-items:center">
            <input type="checkbox" id="dcg-distill-review-include-pinned" /> Include already pinned
          </label>
          <span class="dcg-help" id="dcg-distill-review-status"></span>
        </div>
        <div id="dcg-distill-review-list" class="dcg-debug-list">
          <p class="dcg-help" style="margin:0">Loading…</p>
        </div>
      </div>
      <div class="dcg-section">
        <h4>Shared Server Lore</h4>
        <div class="dcg-help">Guild/channel facts (“deploy day is Thursday”) shared across the server — separate from per-user profiles. Pick a server (and optional channel) from connected Discord guilds.</div>
        <div class="dcg-target-toolbar">
          <button type="button" class="dcg-btn" id="dcg-lore-targets-refresh">Refresh servers</button>
          <span class="dcg-help" id="dcg-lore-targets-status"></span>
        </div>
        <div class="dcg-row" style="gap:8px;flex-wrap:wrap;margin:8px 0">
          <select class="dcg-select" id="dcg-lore-guild" style="min-width:180px;flex:1">
            <option value="">All servers / no filter</option>
          </select>
          <select class="dcg-select" id="dcg-lore-channel" style="min-width:180px;flex:1">
            <option value="">Whole server (guild-wide)</option>
          </select>
          <input class="dcg-input" id="dcg-lore-content" placeholder="New lore fact" style="min-width:220px;flex:2" />
          <button type="button" class="dcg-btn" id="dcg-lore-add">Add lore</button>
          <button type="button" class="dcg-btn" id="dcg-lore-refresh">Refresh list</button>
        </div>
        <label class="dcg-help" style="display:flex;gap:6px;align-items:center;margin-bottom:6px">
          <input type="checkbox" id="dcg-lore-show-forgotten" /> Show soft-forgotten
        </label>
        <div id="dcg-lore-list" class="dcg-target-picker">
          <p class="dcg-help" style="margin:0">No lore yet.</p>
        </div>
      </div>
      <div class="dcg-section">
        <h4>Memory test pathways</h4>
        <div class="dcg-help">Dry-run helpers that write into the plugin SQLite only — useful for verifying milestones, lore, interest graphs, ambient distill, and prompt context without waiting for live chat.</div>
        <div class="dcg-row" style="gap:8px;flex-wrap:wrap;margin:8px 0">
          <input class="dcg-input" id="dcg-mem-test-user" placeholder="User id for tests" style="min-width:160px;flex:1" />
          <select class="dcg-select" id="dcg-mem-test-guild" style="min-width:180px;flex:1">
            <option value="">Guild for lore/context tests</option>
          </select>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">
          <button type="button" class="dcg-btn" id="dcg-mem-test-milestone">Seed milestone</button>
          <button type="button" class="dcg-btn" id="dcg-mem-test-interact">Simulate return + interests</button>
          <button type="button" class="dcg-btn" id="dcg-mem-test-lore">Seed lore</button>
          <button type="button" class="dcg-btn" id="dcg-mem-test-interest">Seed interest</button>
          <button type="button" class="dcg-btn" id="dcg-mem-test-context">Preview context</button>
          <button type="button" class="dcg-btn" id="dcg-mem-test-distill">Run ambient distill</button>
        </div>
        <pre id="dcg-mem-test-output" class="dcg-help" style="white-space:pre-wrap;margin:0;max-height:220px;overflow:auto">Run a test to see results.</pre>
      </div>
  `;

  const greetingSection = `
      <div class="dcg-section">
        <h4>Greeting Channels</h4>
        <div class="dcg-help">Channels for morning greetings, quiet outreach, and goodnight. Loaded from connected servers — check the ones you want.</div>
        <div id="dcg-target-chips" class="dcg-target-chips"><span class="dcg-help">None selected</span></div>
        <div class="dcg-target-toolbar">
          <button type="button" class="dcg-btn" id="dcg-target-refresh">Refresh from Discord</button>
          <button type="button" class="dcg-btn" id="dcg-target-select-all">Select all</button>
          <button type="button" class="dcg-btn" id="dcg-target-clear">Clear all</button>
          <span class="dcg-help" id="dcg-target-status"></span>
        </div>
        <div id="dcg-target-picker" class="dcg-target-picker">
          <p class="dcg-help" style="margin:0">Connect a bot, then click Refresh to load servers and channels.</p>
        </div>
        <textarea class="dcg-textarea" id="proactive.greeting_targets" data-field="proactive.greeting_targets" style="display:none" aria-hidden="true">${esc(greetingTargets.join('\n'))}</textarea>
      </div>
  `;

  const allowlistSection = `
      <div class="dcg-section">
        <h4>Ignored channels</h4>
        <p class="dcg-help">Fully ignore these text channels: no replies, reactions, ambient learning from them, or proactive messages. Uses the same channel list as Greeting Channels. Save after changing.</p>
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

  const presenceSection = `
      <div class="dcg-section">
        <h4>Presence Activity Presets</h4>
        <div class="dcg-help">Checked presets are included in the rotation pool when cycling is enabled (Presence tab). Reload plugin after changing presets file on disk.</div>
        <div id="dcg-presence-preset-picker" class="dcg-target-picker">
          <p class="dcg-help" style="margin:0">Loading presets…</p>
        </div>
        <textarea class="dcg-textarea" id="presence.activity_presets" data-field="presence.activity_presets" style="display:none" aria-hidden="true">${esc(activityPresets.join('\n'))}</textarea>
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

  const proactiveTestSection = `
      <div class="dcg-section">
        <div class="dcg-proactive-test" style="padding:12px;border:1px solid var(--border);border-radius:8px">
          <strong>Test proactive pathways</strong>
          <div class="dcg-help" style="margin:6px 0 10px">
            Manually fire morning greeting, goodnight, or quiet outreach to connected greeting channels.
            Shows schedule diagnostics so you can see why the automatic cron may have skipped (wrong hour, no targets, daemon offline, etc.).
          </div>
          <label style="display:block;margin:8px 0;font-size:0.92em">
            <input type="checkbox" id="dcg-proactive-dry-run"> Dry run (preview message only — do not post to Discord)
          </label>
          <div class="dcg-target-toolbar" style="margin:8px 0">
            <button type="button" class="dcg-btn" id="dcg-test-greeting">Test morning greeting</button>
            <button type="button" class="dcg-btn" id="dcg-test-goodnight">Test goodnight</button>
            <button type="button" class="dcg-btn" id="dcg-test-outreach">Test quiet outreach</button>
            <button type="button" class="dcg-btn" id="dcg-refresh-proactive-diag">Refresh diagnostics</button>
          </div>
          <pre id="dcg-proactive-test-output" class="dcg-help" style="white-space:pre-wrap;max-height:280px;overflow:auto;margin:8px 0 0;font-size:0.82em"></pre>
        </div>
      </div>
  `;

  const debugSection = `
      <div class="dcg-section">
        <h4>Cognition preview</h4>
        <p class="dcg-help">Live social judgment from the daemon: last channel situations, intention scores (reply / react / silent), and gate multipliers. Empty until chat or outreach runs with Cognition settings on.</p>
        <div class="dcg-target-toolbar">
          <button type="button" class="dcg-btn" id="dcg-cognition-refresh">Refresh now</button>
          <span class="dcg-help" id="dcg-cognition-status"></span>
        </div>
        <div id="dcg-cognition-flags" class="dcg-help" style="margin:6px 0 10px"></div>
        <div id="dcg-cognition-panel">
          <p class="dcg-help" style="margin:0">Loading…</p>
        </div>
      </div>
      <div class="dcg-section">
        <h4>Decision traces</h4>
        <p class="dcg-help">Recent world-model / policy / delivery traces — why she replied, stayed quiet, skipped outreach, distilled facts, etc. Filter by type optional.</p>
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
    { tab: 'Proactive', mount: (el) => { el.innerHTML = greetingSection; } },
    { tab: 'Proactive', mount: (el) => { el.innerHTML = proactiveTestSection; } },
    { tab: 'Presence', mount: (el) => { el.innerHTML = presenceSection; } },
    { tab: 'Voice', mount: (el) => { el.innerHTML = voiceJoinSection; } },
    { tab: 'Voice', mount: (el) => { el.innerHTML = voicePromptSection; } },
    { tab: 'Media', mount: (el) => { el.innerHTML = visionLlmSection; } },
    { tab: 'Memory', mount: (el) => { el.innerHTML = memorySection; } },
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

  bindProactiveTestPanel(container);
  const voicePromptField = fieldByData(container, 'voice.conversation_prompt_template');
  if (voicePromptField && voicePromptDefault) {
    voicePromptField.dataset.defaultTemplate = voicePromptDefault;
  }
  bindAccounts(container);
  initMemoryBrowser(container, data.accounts?.accounts || []);
  initDistillReviewPanel(container, data.accounts?.accounts || []);
  initDebugPanel(container, data.llmDebug?.entries || []);
  initCognitionPanel(container, data.cognitionDebug || null);
  initTracesPanel(container, data.traces || null);
  initGreetingTargetPicker(container, greetingTargets);
  initIgnoredChannelPicker(container, ignoredChannels);
  initBotAllowlistPicker(container, allowlistIds);
  initVoiceTargetPicker(container, joinTargets);
  initPresencePresetPicker(container, activityPresets);
  initLlmProviderBlocks(container, {
    reply: {
      prefix: 'dcg-llm',
      providerKey: replyProvider,
      modelName: replyModel,
      autoLabel: 'Daemon chooses (default)',
    },
    greeting: {
      prefix: 'dcg-greeting-llm',
      providerKey: greetingProvider || replyProvider,
      modelName: greetingModel || replyModel,
      inheritsReply: !greetingProvider,
    },
    goodnight: {
      prefix: 'dcg-goodnight-llm',
      providerKey: goodnightProvider || greetingProvider || replyProvider,
      modelName: goodnightModel || greetingModel || replyModel,
      inheritsGreeting: !goodnightProvider,
    },
    distill: {
      prefix: 'dcg-distill-llm',
      providerKey: distillProvider || replyProvider,
      modelName: distillModel || replyModel,
      inheritsReply: !distillProvider,
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
const _llmInheritState = {
  greetingInheritsReply: false,
  goodnightInheritsGreeting: false,
  distillInheritsReply: false,
};

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

function syncInheritedLlmBlocks(container) {
  if (_llmInheritState.greetingInheritsReply) {
    const reply = readLlmBlockValues(container, 'dcg-llm');
    const greeting = llmBlockElements(container, 'dcg-greeting-llm');
    if (greeting.primary) {
      greeting.primary.innerHTML = llmProviderOptionsHtml(reply.provider);
      greeting.primary.value = reply.provider;
      updateLlmModelSelector(container, 'dcg-greeting-llm', reply.provider, reply.model);
    }
  }
  if (_llmInheritState.goodnightInheritsGreeting) {
    const greeting = readLlmBlockValues(container, 'dcg-greeting-llm');
    const goodnight = llmBlockElements(container, 'dcg-goodnight-llm');
    if (goodnight.primary) {
      goodnight.primary.innerHTML = llmProviderOptionsHtml(greeting.provider);
      goodnight.primary.value = greeting.provider;
      updateLlmModelSelector(container, 'dcg-goodnight-llm', greeting.provider, greeting.model);
    }
  }
  if (_llmInheritState.distillInheritsReply) {
    const reply = readLlmBlockValues(container, 'dcg-llm');
    const distill = llmBlockElements(container, 'dcg-distill-llm');
    if (distill.primary) {
      distill.primary.innerHTML = llmProviderOptionsHtml(reply.provider);
      distill.primary.value = reply.provider;
      updateLlmModelSelector(container, 'dcg-distill-llm', reply.provider, reply.model);
    }
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
    if (prefix === 'dcg-greeting-llm') {
      syncInheritedLlmBlocks(container);
    }
  });

  els.modelSelect?.addEventListener('change', (event) => {
    if (els.modelField) els.modelField.value = event.target.value || '';
    if (prefix === 'dcg-greeting-llm') {
      _llmInheritState.greetingInheritsReply = false;
      syncInheritedLlmBlocks(container);
    } else if (onProviderChange) {
      onProviderChange();
    }
  });

  els.modelCustom?.addEventListener('input', (event) => {
    if (els.modelField) els.modelField.value = (event.target.value || '').trim();
    if (prefix === 'dcg-greeting-llm') {
      _llmInheritState.greetingInheritsReply = false;
      syncInheritedLlmBlocks(container);
    } else if (onProviderChange) {
      onProviderChange();
    }
  });
}

function initLlmProviderBlocks(container, blocks) {
  _llmInheritState.greetingInheritsReply = !!blocks.greeting?.inheritsReply;
  _llmInheritState.goodnightInheritsGreeting = !!blocks.goodnight?.inheritsGreeting;
  _llmInheritState.distillInheritsReply = !!blocks.distill?.inheritsReply;

  initLlmProviderBlock(container, {
    ...blocks.reply,
    onProviderChange: () => syncInheritedLlmBlocks(container),
  });
  initLlmProviderBlock(container, {
    ...blocks.greeting,
    onProviderChange: () => {
      _llmInheritState.greetingInheritsReply = false;
      syncInheritedLlmBlocks(container);
    },
  });
  initLlmProviderBlock(container, {
    ...blocks.goodnight,
    onProviderChange: () => {
      _llmInheritState.goodnightInheritsGreeting = false;
    },
  });
  if (blocks.distill) {
    initLlmProviderBlock(container, {
      ...blocks.distill,
      onProviderChange: () => {
        _llmInheritState.distillInheritsReply = false;
      },
    });
  }
  if (blocks.voice) {
    initLlmProviderBlock(container, { ...blocks.voice });
  }
  if (blocks.vision) {
    initLlmProviderBlock(container, { ...blocks.vision });
  }
}

// Inheritance blanking on the flat dotted keyspace: when a proactive block
// matches what it would inherit anyway, store '' so it keeps following.
function applyProactiveLlmInheritance(flat) {
  const replyProvider = flat['cognitive.llm_primary'] || 'auto';
  const replyModel = flat['cognitive.llm_model'] || '';

  if ((flat['proactive.greeting_model_provider'] || 'auto') === replyProvider
    && (flat['proactive.greeting_model_name'] || '') === replyModel) {
    flat['proactive.greeting_model_provider'] = '';
    flat['proactive.greeting_model_name'] = '';
  }

  const greetingProvider = flat['proactive.greeting_model_provider'] || replyProvider;
  const greetingModel = flat['proactive.greeting_model_name'] || replyModel;
  if ((flat['proactive.goodnight_model_provider'] || greetingProvider) === greetingProvider
    && (flat['proactive.goodnight_model_name'] || greetingModel) === greetingModel) {
    flat['proactive.goodnight_model_provider'] = '';
    flat['proactive.goodnight_model_name'] = '';
  }

  if ((flat['profile.distill_model_provider'] || 'auto') === replyProvider
    && (flat['profile.distill_model_name'] || '') === replyModel) {
    flat['profile.distill_model_provider'] = '';
    flat['profile.distill_model_name'] = '';
  }
}

const greetingTargetCatalog = {};
let greetingTargetSelection = new Set();
const ignoredChannelCatalog = {};
let ignoredChannelSelection = new Set();
/** Cached proactive/targets rows for Memory lore dropdowns: account -> targets[] */
const loreTargetsByAccount = {};
const loreGuildNames = {};
const loreChannelNames = {};
const presencePresetCatalog = {};
let presencePresetSelection = new Set();
let presencePresetDefaults = [];

// ── LLM debug panel (Debug tab slot) ──

let _debugRefreshTimer = null;
let _cognitionRefreshTimer = null;

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
        ${prompt.memory_recalled || prompt.memory_pinned ? `<div class="dcg-debug-label">Prompt — memory</div><div class="dcg-debug-block">Recalled: ${Number(prompt.memory_recalled) || 0} · Pinned: ${Number(prompt.memory_pinned) || 0}${prompt.profile_summary ? `\nProfile: ${esc(prompt.profile_summary)}` : ''}</div>` : ''}
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
  if (_debugRefreshTimer) {
    clearInterval(_debugRefreshTimer);
    _debugRefreshTimer = null;
  }
  _debugRefreshTimer = setInterval(() => {
    if (container.isConnected) refreshDebugPanel(container);
  }, 15000);
}

function formatSilence(seconds) {
  const s = Number(seconds) || 0;
  if (s >= 3600) return `${(s / 3600).toFixed(1)}h quiet`;
  if (s >= 60) return `${Math.round(s / 60)}m quiet`;
  return `${Math.round(s)}s quiet`;
}

function renderCognitionPanel(panelEl, flagsEl, data) {
  if (!panelEl) return;
  const settings = data?.settings || {};
  if (flagsEl) {
    const flags = [
      settings.situation_enabled ? 'situation on' : 'situation off',
      settings.situation_in_prompt ? 'prompt inject' : 'no prompt inject',
      settings.intention_competition_enabled ? 'competition on' : 'competition off',
      settings.relationship_policy_enabled
        ? `relationship ${settings.relationship_policy_strength || 'normal'}`
        : 'relationship off',
      settings.situation_presence_enabled ? 'presence bias on' : 'presence bias off',
    ];
    flagsEl.textContent = `Flags: ${flags.join(' · ')}`;
  }
  const situations = data?.situations || [];
  const intentions = data?.intentions || [];
  const gates = data?.gates || [];
    if (!situations.length && !intentions.length && !gates.length) {
    panelEl.innerHTML = '<p class="dcg-help" style="margin:0">No cognition events yet. If she is asleep, unaddressed chat is dropped before scoring — @mention her, disable sleep for the channel, or wait until wake. Then Refresh.</p>';
    return;
  }
  const sitHtml = situations.length
    ? situations.slice(0, 8).map((s) => `
        <details class="dcg-debug-entry">
          <summary><span class="dcg-debug-badge dcg-debug-badge-pending">${esc(s.vibe || '—')}</span> ${esc(s.channel_name || s.channel_id || 'channel')} · mult ${esc(Number(s.organic_multiplier || 1).toFixed(2))}</summary>
          <div class="dcg-debug-meta">${esc(s.account || '')} · ${esc(formatSilence(s.silence_seconds))} · heat ${esc(Number(s.heat || 0).toFixed(2))} · ${esc(Number(s.message_count) || 0)} msgs · ${esc(formatDebugTimestamp(s.built_at))}</div>
          <div class="dcg-debug-block">${esc(s.summary || '—')}${(s.recent_topics || []).length ? `\nTopics: ${(s.recent_topics || []).map(esc).join(', ')}` : ''}</div>
        </details>`).join('')
    : '<p class="dcg-help">No situations cached.</p>';
  const intentHtml = intentions.length
    ? intentions.slice(0, 10).map((i) => `
        <details class="dcg-debug-entry">
          <summary><span class="dcg-debug-badge ${i.kind === 'reply' ? 'dcg-debug-badge-ok' : 'dcg-debug-badge-warn'}">${esc(i.kind || '—')}</span> ${esc(i.username || 'someone')} · ${esc(i.channel_name || i.channel_id || '')}</summary>
          <div class="dcg-debug-meta">${esc(formatDebugTimestamp(i.at))} · score ${esc(Number(i.score || 0).toFixed(3))} · ${esc(i.reason || '')} · vibe ${esc(i.situation_vibe || '—')}</div>
          <div class="dcg-debug-block">organic×${esc(Number(i.organic_multiplier || 1).toFixed(2))} · react×${esc(Number(i.reaction_multiplier || 1).toFixed(2))}
${i.relationship && Object.keys(i.relationship).length ? `Relationship: fam ${Number(i.relationship.familiarity || 0).toFixed(2)} · fond ${Number(i.relationship.fondness || 0).toFixed(2)}` : ''}</div>
        </details>`).join('')
    : '<p class="dcg-help">No intention scores yet (enable Intention competition, or wait for organic rolls to show under Gates).</p>';
  const gateHtml = gates.length
    ? gates.slice(0, 10).map((g) => `
        <details class="dcg-debug-entry">
          <summary>${esc(g.gate || 'gate')} · ${esc(g.channel_name || g.channel_id || '')}</summary>
          <div class="dcg-debug-meta">${esc(formatDebugTimestamp(g.at))} · ${esc(g.account || '')}</div>
          <div class="dcg-debug-block">${esc(JSON.stringify(g.detail || {}, null, 2))}</div>
        </details>`).join('')
    : '<p class="dcg-help">No gate events yet.</p>';
  panelEl.innerHTML = `
    <div class="dcg-debug-label">Last situations (per channel)</div>
    <div class="dcg-debug-list">${sitHtml}</div>
    <div class="dcg-debug-label" style="margin-top:12px">Recent intentions</div>
    <div class="dcg-debug-list">${intentHtml}</div>
    <div class="dcg-debug-label" style="margin-top:12px">Recent gates</div>
    <div class="dcg-debug-list">${gateHtml}</div>
  `;
}

async function refreshCognitionPanel(container) {
  const panel = container.querySelector('#dcg-cognition-panel');
  const flags = container.querySelector('#dcg-cognition-flags');
  const status = container.querySelector('#dcg-cognition-status');
  if (!panel) return;
  if (status) status.textContent = 'Refreshing…';
  try {
    const data = await api('debug/cognition');
    renderCognitionPanel(panel, flags, data);
    if (status) {
      status.textContent = data.daemon_running
        ? `Updated ${new Date().toLocaleTimeString()} · daemon running`
        : `Updated ${new Date().toLocaleTimeString()} · daemon offline`;
    }
  } catch (err) {
    if (status) status.textContent = err.message;
    panel.innerHTML = `<p class="dcg-help" style="margin:0;color:var(--error)">${esc(err.message)}</p>`;
  }
}

function initCognitionPanel(container, initialData = null) {
  const panel = container.querySelector('#dcg-cognition-panel');
  const flags = container.querySelector('#dcg-cognition-flags');
  const refreshBtn = container.querySelector('#dcg-cognition-refresh');
  if (!panel) return;
  if (initialData) renderCognitionPanel(panel, flags, initialData);
  else panel.innerHTML = '<p class="dcg-help" style="margin:0">Loading…</p>';
  refreshBtn?.addEventListener('click', () => refreshCognitionPanel(container));
  if (_cognitionRefreshTimer) {
    clearInterval(_cognitionRefreshTimer);
    _cognitionRefreshTimer = null;
  }
  _cognitionRefreshTimer = setInterval(() => {
    if (container.isConnected) refreshCognitionPanel(container);
  }, 15000);
  if (!initialData) refreshCognitionPanel(container);
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
    listEl.innerHTML = '<p class="dcg-help" style="margin:0">No traces yet — chat or wait for proactive/cron activity while the daemon runs.</p>';
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

function personLabel(row) {
  return row.display_name || row.username || row.user_id || 'unknown';
}

function renderDistillReviewList(listEl, facts) {
  if (!listEl) return;
  if (!facts.length) {
    listEl.innerHTML = '<p class="dcg-help" style="margin:0">No pending ambient facts — queue is clear (or distill has not run yet).</p>';
    return;
  }
  listEl.innerHTML = facts.map((f) => `
    <div class="dcg-debug-entry" style="padding:8px" data-fact-id="${esc(String(f.id))}">
      <div class="dcg-debug-meta">${esc(personLabel(f))} · ${esc(f.source || 'ambient_distill')} · ${esc(formatDebugTimestamp(f.created_at))}${Number(f.pinned) ? ' · pinned' : ''}</div>
      <div class="dcg-debug-block" style="margin-bottom:6px">${esc(f.content || '')}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button type="button" class="dcg-btn dcg-review-pin" data-id="${esc(String(f.id))}">${Number(f.pinned) ? 'Unpin' : 'Pin (approve)'}</button>
        <button type="button" class="dcg-btn dcg-review-forget" data-id="${esc(String(f.id))}">Soft-forget</button>
      </div>
    </div>
  `).join('');
}

async function refreshDistillReviewPanel(container) {
  const list = container.querySelector('#dcg-distill-review-list');
  const status = container.querySelector('#dcg-distill-review-status');
  const accountSelect = container.querySelector('#dcg-mem-account');
  const includePinned = container.querySelector('#dcg-distill-review-include-pinned')?.checked;
  if (!list) return;
  const account = accountSelect?.value || '';
  if (!account) {
    list.innerHTML = '<p class="dcg-help" style="margin:0">Select a bot account above.</p>';
    return;
  }
  if (status) status.textContent = 'Loading…';
  try {
    const pending = includePinned ? '0' : '1';
    const data = await api(`profiles/facts/review?account=${encodeURIComponent(account)}&pending_only=${pending}&limit=40`);
    renderDistillReviewList(list, data.facts || []);
    list.querySelectorAll('.dcg-review-pin').forEach((btn) => {
      btn.addEventListener('click', async () => {
        try {
          const pinned = btn.textContent.includes('Unpin');
          await api('profiles/facts/update', {
            method: 'POST',
            body: JSON.stringify({ fact_id: Number(btn.dataset.id), action: pinned ? 'unpin' : 'pin' }),
          });
          await refreshDistillReviewPanel(container);
        } catch (err) {
          if (status) status.textContent = err.message;
        }
      });
    });
    list.querySelectorAll('.dcg-review-forget').forEach((btn) => {
      btn.addEventListener('click', async () => {
        try {
          await api('profiles/facts/update', {
            method: 'POST',
            body: JSON.stringify({ fact_id: Number(btn.dataset.id), action: 'soft_forget' }),
          });
          await refreshDistillReviewPanel(container);
        } catch (err) {
          if (status) status.textContent = err.message;
        }
      });
    });
    if (status) status.textContent = `${(data.facts || []).length} fact(s)`;
  } catch (err) {
    if (status) status.textContent = err.message;
    list.innerHTML = `<p class="dcg-help" style="margin:0;color:var(--error)">${esc(err.message)}</p>`;
  }
}

function initDistillReviewPanel(container, accounts) {
  const list = container.querySelector('#dcg-distill-review-list');
  if (!list) return;
  const refreshBtn = container.querySelector('#dcg-distill-review-refresh');
  const includePinned = container.querySelector('#dcg-distill-review-include-pinned');
  const accountSelect = container.querySelector('#dcg-mem-account');
  refreshBtn?.addEventListener('click', () => refreshDistillReviewPanel(container));
  includePinned?.addEventListener('change', () => refreshDistillReviewPanel(container));
  accountSelect?.addEventListener('change', () => refreshDistillReviewPanel(container));
  if ((accounts || []).length) refreshDistillReviewPanel(container);
  else list.innerHTML = '<p class="dcg-help" style="margin:0">Add a bot account first.</p>';
}

// ── Memory browser (Memory tab slot) ──

function memoryUserLabel(row) {
  return row.display_name || row.username || row.birthday_display_name
    || row.birthday_username || row.user_id;
}

function initMemoryBrowser(container, accounts) {
  const select = container.querySelector('#dcg-mem-account');
  const list = container.querySelector('#dcg-mem-list');
  const status = container.querySelector('#dcg-mem-status');
  if (!select || !list) return;

  const names = (accounts || []).map((a) => a.name).filter(Boolean);
  select.innerHTML = names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join('')
    || '<option value="">no bots configured</option>';

  const setStatus = (text) => { if (status) status.textContent = text || ''; };

  const factActions = (fact) => `
    <span style="display:inline-flex;gap:4px;margin-left:8px">
      <button type="button" class="dcg-btn dcg-fact-pin" data-id="${fact.id}" data-pinned="${Number(fact.pinned) ? '1' : '0'}">${Number(fact.pinned) ? 'Unpin' : 'Pin'}</button>
      <button type="button" class="dcg-btn dcg-fact-edit" data-id="${fact.id}">Edit</button>
      <button type="button" class="dcg-btn dcg-fact-soft" data-id="${fact.id}">Soft-forget</button>
    </span>`;

  const bindFactButtons = (box, account, userId, reloadFacts) => {
    box.querySelectorAll('.dcg-fact-pin').forEach((btn) => {
      btn.addEventListener('click', async () => {
        try {
          const pinned = btn.dataset.pinned === '1';
          await api('profiles/facts/update', {
            method: 'POST',
            body: JSON.stringify({ fact_id: Number(btn.dataset.id), action: pinned ? 'unpin' : 'pin' }),
          });
          await reloadFacts();
        } catch (err) { setStatus(`Pin failed: ${err.message}`); }
      });
    });
    box.querySelectorAll('.dcg-fact-edit').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const row = btn.closest('.dcg-fact-row');
        const current = row?.dataset.content || '';
        const next = window.prompt('Edit fact', current);
        if (next == null || !String(next).trim()) return;
        try {
          await api('profiles/facts/update', {
            method: 'POST',
            body: JSON.stringify({ fact_id: Number(btn.dataset.id), action: 'edit', content: String(next).trim() }),
          });
          await reloadFacts();
        } catch (err) { setStatus(`Edit failed: ${err.message}`); }
      });
    });
    box.querySelectorAll('.dcg-fact-soft').forEach((btn) => {
      btn.addEventListener('click', async () => {
        try {
          await api('profiles/facts/update', {
            method: 'POST',
            body: JSON.stringify({ fact_id: Number(btn.dataset.id), action: 'soft_forget' }),
          });
          await reloadFacts();
        } catch (err) { setStatus(`Soft-forget failed: ${err.message}`); }
      });
    });
    box.querySelectorAll('.dcg-fact-restore').forEach((btn) => {
      btn.addEventListener('click', async () => {
        try {
          await api('profiles/facts/update', {
            method: 'POST',
            body: JSON.stringify({ fact_id: Number(btn.dataset.id), action: 'restore' }),
          });
          await reloadFacts();
        } catch (err) { setStatus(`Restore failed: ${err.message}`); }
      });
    });
    box.querySelector('#dcg-fact-add')?.addEventListener('click', async () => {
      const input = box.querySelector('#dcg-fact-new');
      const content = input?.value?.trim();
      if (!content) return;
      try {
        await api('profiles/facts', {
          method: 'POST',
          body: JSON.stringify({ account: account, user_id: userId, content }),
        });
        if (input) input.value = '';
        await reloadFacts();
      } catch (err) { setStatus(`Add fact failed: ${err.message}`); }
    });
  };

  const renderFactPanel = async (box, account, userId) => {
    box.innerHTML = '<span class="dcg-help">Loading…</span>';
    try {
      const [factsData, milesData, interestData] = await Promise.all([
        api(`profiles/facts?account=${encodeURIComponent(account)}&user=${encodeURIComponent(userId)}&include_forgotten=1`),
        api(`profiles/milestones?account=${encodeURIComponent(account)}&user=${encodeURIComponent(userId)}`),
        api(`profiles/interests?account=${encodeURIComponent(account)}&user=${encodeURIComponent(userId)}`),
      ]);
      const facts = factsData.facts || [];
      const milestones = milesData.milestones || [];
      const interests = interestData.interests || [];
      const active = facts.filter((f) => !Number(f.forgotten));
      const forgotten = facts.filter((f) => Number(f.forgotten));
      const factHtml = active.length
        ? active.map((f) => `
            <div class="dcg-fact-row dcg-help" data-content="${esc(f.content)}" style="margin:4px 0">
              ${Number(f.pinned) ? '📌 ' : '• '}${esc(f.content)}
              <em>(${esc(f.source || '')})</em>${factActions(f)}
            </div>`).join('')
        : '<div class="dcg-help">No stored facts.</div>';
      const forgottenHtml = forgotten.length
        ? `<details style="margin-top:6px"><summary class="dcg-help">Soft-forgotten (${forgotten.length})</summary>${
            forgotten.map((f) => `
              <div class="dcg-fact-row dcg-help" data-content="${esc(f.content)}" style="margin:4px 0;opacity:.7">
                • ${esc(f.content)}
                <button type="button" class="dcg-btn dcg-fact-restore" data-id="${f.id}">Restore</button>
              </div>`).join('')
          }</details>`
        : '';
      const mileHtml = milestones.length
        ? `<div class="dcg-help" style="margin-top:8px"><strong>Milestones</strong><br>${
            milestones.map((m) => `• ${esc(m.detail || m.milestone_type)}${Number(m.acknowledged) ? '' : ' <em>(pending)</em>'}`).join('<br>')
          }</div>`
        : '<div class="dcg-help" style="margin-top:8px">No milestones yet.</div>';
      const interestHtml = interests.length
        ? `<div class="dcg-help" style="margin-top:8px"><strong>Interests</strong><br>${
            interests.map((t) => `• ${esc(t.topic)} <em>(w=${Number(t.weight).toFixed(1)}, n=${t.mention_count})</em>`).join('<br>')
          }</div>`
        : '<div class="dcg-help" style="margin-top:8px">No interest topics yet.</div>';
      box.innerHTML = `
        ${factHtml}
        <div style="display:flex;gap:6px;margin-top:8px">
          <input class="dcg-input" id="dcg-fact-new" placeholder="Add a fact…" style="flex:1" />
          <button type="button" class="dcg-btn" id="dcg-fact-add">Add</button>
        </div>
        ${forgottenHtml}
        ${mileHtml}
        ${interestHtml}`;
      bindFactButtons(box, account, userId, () => renderFactPanel(box, account, userId));
    } catch (err) {
      box.innerHTML = `<span class="dcg-status-err">${esc(err.message)}</span>`;
    }
  };

  const renderRows = (profiles, account) => {
    if (!profiles.length) {
      list.innerHTML = '<p class="dcg-help" style="margin:0">No users in memory for this bot yet — she learns people as they talk.</p>';
      return;
    }
    list.innerHTML = profiles.map((row) => {
      const birthday = Number(row.birthday_month)
        ? ` · 🎂 ${String(row.birthday_month).padStart(2, '0')}-${String(row.birthday_day).padStart(2, '0')}`
        : '';
      return `
        <div class="dcg-mem-row" data-user="${esc(row.user_id)}" style="padding:6px 0;border-bottom:1px solid var(--border)">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
            <span><strong>${esc(memoryUserLabel(row))}</strong>
              <span class="dcg-help">${Number(row.message_count) || 0} messages${birthday}</span></span>
            <span style="display:flex;gap:6px">
              <button type="button" class="dcg-btn dcg-mem-facts">Browse</button>
              <button type="button" class="dcg-btn dcg-btn-danger dcg-mem-forget">Forget</button>
            </span>
          </div>
          <div class="dcg-mem-fact-list" style="display:none;margin:6px 0 0 8px"></div>
        </div>`;
    }).join('');

    list.querySelectorAll('.dcg-mem-facts').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const rowEl = btn.closest('.dcg-mem-row');
        const box = rowEl.querySelector('.dcg-mem-fact-list');
        if (box.style.display !== 'none') { box.style.display = 'none'; return; }
        box.style.display = 'block';
        await renderFactPanel(box, account, rowEl.dataset.user);
      });
    });

    list.querySelectorAll('.dcg-mem-forget').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const rowEl = btn.closest('.dcg-mem-row');
        const label = rowEl.querySelector('strong')?.textContent || rowEl.dataset.user;
        if (!window.confirm(`Forget everything about ${label}? This deletes their profile, facts, milestones, interests, and pinned memories.`)) return;
        try {
          await api('admin/forget-user', {
            method: 'POST',
            body: JSON.stringify({ account_name: account, user_id: rowEl.dataset.user }),
          });
          rowEl.remove();
          setStatus(`Forgot ${label}.`);
        } catch (err) {
          setStatus(`Forget failed: ${err.message}`);
        }
      });
    });
  };

  const loadList = async () => {
    const account = select.value;
    if (!account) {
      list.innerHTML = '<p class="dcg-help" style="margin:0">Add a bot account first.</p>';
      return;
    }
    setStatus('Loading…');
    try {
      const data = await api(`profiles?account=${encodeURIComponent(account)}`);
      renderRows(data.profiles || [], account);
      setStatus('');
    } catch (err) {
      setStatus(`Load failed: ${err.message}`);
    }
  };

  const loreList = container.querySelector('#dcg-lore-list');
  const loreGuildSelect = container.querySelector('#dcg-lore-guild');
  const loreChannelSelect = container.querySelector('#dcg-lore-channel');
  const loreTargetsStatus = container.querySelector('#dcg-lore-targets-status');
  const testGuildSelect = container.querySelector('#dcg-mem-test-guild');

  const loreScopeLabel = (guildId, channelId) => {
    if (!guildId && !channelId) return 'all servers';
    const guildName = loreGuildNames[guildId] || guildId || 'server';
    if (!channelId) return `${guildName} (whole server)`;
    const channelName = loreChannelNames[channelId] || channelId;
    return `${guildName} · #${channelName}`;
  };

  const fillLoreGuildOptions = (account, { preserve = true } = {}) => {
    const prevGuild = preserve ? (loreGuildSelect?.value || '') : '';
    const prevTestGuild = preserve ? (testGuildSelect?.value || '') : '';
    const targets = (loreTargetsByAccount[account] || []).filter((t) => t.account === account);
    const guilds = [];
    const seen = new Set();
    targets.forEach((t) => {
      if (!t.guild_id || seen.has(t.guild_id)) return;
      seen.add(t.guild_id);
      guilds.push({ id: t.guild_id, name: t.guild_name || t.guild_id });
      loreGuildNames[t.guild_id] = t.guild_name || t.guild_id;
      loreChannelNames[t.channel_id] = t.channel_name || t.channel_id;
    });
    guilds.sort((a, b) => a.name.localeCompare(b.name));
    if (loreGuildSelect) {
      loreGuildSelect.innerHTML = '<option value="">All servers / no filter</option>'
        + guilds.map((g) => `<option value="${esc(g.id)}">${esc(g.name)}</option>`).join('');
      if (prevGuild && [...loreGuildSelect.options].some((o) => o.value === prevGuild)) {
        loreGuildSelect.value = prevGuild;
      }
    }
    if (testGuildSelect) {
      testGuildSelect.innerHTML = '<option value="">Guild for lore/context tests</option>'
        + guilds.map((g) => `<option value="${esc(g.id)}">${esc(g.name)}</option>`).join('');
      if (prevTestGuild && [...testGuildSelect.options].some((o) => o.value === prevTestGuild)) {
        testGuildSelect.value = prevTestGuild;
      } else if (!prevTestGuild && guilds.length) {
        testGuildSelect.value = guilds[0].id;
      }
    }
    fillLoreChannelOptions(account, { preserve });
  };

  const fillLoreChannelOptions = (account, { preserve = true } = {}) => {
    if (!loreChannelSelect) return;
    const prevChannel = preserve ? (loreChannelSelect.value || '') : '';
    const guildId = loreGuildSelect?.value || '';
    const targets = (loreTargetsByAccount[account] || []).filter((t) => t.account === account);
    const channels = targets
      .filter((t) => !guildId || t.guild_id === guildId)
      .map((t) => ({ id: t.channel_id, name: t.channel_name || t.channel_id, guild_id: t.guild_id }));
    // Dedup by channel id
    const seen = new Set();
    const unique = [];
    channels.forEach((c) => {
      if (!c.id || seen.has(c.id)) return;
      seen.add(c.id);
      unique.push(c);
      loreChannelNames[c.id] = c.name;
    });
    unique.sort((a, b) => a.name.localeCompare(b.name));
    const wholeLabel = guildId ? 'Whole server (guild-wide)' : 'Whole server (pick a server first)';
    loreChannelSelect.innerHTML = `<option value="">${esc(wholeLabel)}</option>`
      + (guildId
        ? unique.map((c) => `<option value="${esc(c.id)}">#${esc(c.name)}</option>`).join('')
        : '');
    if (prevChannel && [...loreChannelSelect.options].some((o) => o.value === prevChannel)) {
      loreChannelSelect.value = prevChannel;
    } else {
      loreChannelSelect.value = '';
    }
  };

  const loadLoreTargets = async () => {
    const account = select.value;
    if (!account) {
      if (loreTargetsStatus) loreTargetsStatus.textContent = 'Select a bot account first.';
      return;
    }
    if (loreTargetsStatus) loreTargetsStatus.textContent = 'Loading servers…';
    try {
      const data = await api('proactive/targets');
      if (data.error && !(data.targets || []).length) throw new Error(data.error);
      const all = data.targets || [];
      const byAccount = {};
      all.forEach((t) => {
        if (!byAccount[t.account]) byAccount[t.account] = [];
        byAccount[t.account].push(t);
        if (t.guild_id) loreGuildNames[t.guild_id] = t.guild_name || t.guild_id;
        if (t.channel_id) loreChannelNames[t.channel_id] = t.channel_name || t.channel_id;
      });
      Object.keys(loreTargetsByAccount).forEach((key) => { delete loreTargetsByAccount[key]; });
      Object.assign(loreTargetsByAccount, byAccount);
      fillLoreGuildOptions(account);
      const count = (loreTargetsByAccount[account] || []).length;
      const guildCount = new Set((loreTargetsByAccount[account] || []).map((t) => t.guild_id)).size;
      if (loreTargetsStatus) {
        loreTargetsStatus.textContent = count
          ? `${guildCount} server${guildCount === 1 ? '' : 's'}, ${count} channel${count === 1 ? '' : 's'}`
          : (data.error || 'No channels for this bot — connect it and refresh');
      }
    } catch (err) {
      if (loreTargetsStatus) loreTargetsStatus.textContent = err.message || 'Failed to load servers';
    }
  };

  const loadLore = async () => {
    const account = select.value;
    if (!loreList || !account) return;
    const guild = loreGuildSelect?.value?.trim() || '';
    const showForgotten = !!container.querySelector('#dcg-lore-show-forgotten')?.checked;
    loreList.innerHTML = '<p class="dcg-help" style="margin:0">Loading…</p>';
    try {
      let path = `lore?account=${encodeURIComponent(account)}&include_forgotten=${showForgotten ? '1' : '0'}`;
      if (guild) path += `&guild=${encodeURIComponent(guild)}`;
      const data = await api(path);
      const rows = data.lore || [];
      if (!rows.length) {
        loreList.innerHTML = '<p class="dcg-help" style="margin:0">No lore yet.</p>';
        return;
      }
      loreList.innerHTML = rows.map((row) => {
        const scope = loreScopeLabel(row.guild_id, row.channel_id);
        const forgotten = Number(row.forgotten);
        return `
          <div class="dcg-lore-row" data-id="${row.id}" style="padding:6px 0;border-bottom:1px solid var(--border);${forgotten ? 'opacity:.65' : ''}">
            <div>${Number(row.pinned) ? '📌 ' : ''}${esc(row.content)}
              <span class="dcg-help"> · ${esc(scope)} · ${esc(row.source || '')}</span></div>
            <div style="display:flex;gap:6px;margin-top:4px">
              <button type="button" class="dcg-btn dcg-lore-pin">${Number(row.pinned) ? 'Unpin' : 'Pin'}</button>
              <button type="button" class="dcg-btn dcg-lore-edit">Edit</button>
              ${forgotten
                ? '<button type="button" class="dcg-btn dcg-lore-restore">Restore</button>'
                : '<button type="button" class="dcg-btn dcg-lore-soft">Soft-forget</button>'}
            </div>
          </div>`;
      }).join('');
      loreList.querySelectorAll('.dcg-lore-pin').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const row = btn.closest('.dcg-lore-row');
          const pinned = btn.textContent === 'Pin';
          await api('lore', {
            method: 'POST',
            body: JSON.stringify({ lore_id: Number(row.dataset.id), action: pinned ? 'pin' : 'unpin' }),
          });
          loadLore();
        });
      });
      loreList.querySelectorAll('.dcg-lore-edit').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const row = btn.closest('.dcg-lore-row');
          const current = row.querySelector('div')?.childNodes?.[0]?.textContent?.replace(/^📌\s*/, '').trim() || '';
          const next = window.prompt('Edit lore', current);
          if (next == null || !String(next).trim()) return;
          await api('lore', {
            method: 'POST',
            body: JSON.stringify({ lore_id: Number(row.dataset.id), action: 'edit', content: String(next).trim() }),
          });
          loadLore();
        });
      });
      loreList.querySelectorAll('.dcg-lore-soft').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const row = btn.closest('.dcg-lore-row');
          await api('lore', {
            method: 'POST',
            body: JSON.stringify({ lore_id: Number(row.dataset.id), action: 'soft_forget' }),
          });
          loadLore();
        });
      });
      loreList.querySelectorAll('.dcg-lore-restore').forEach((btn) => {
        btn.addEventListener('click', async () => {
          const row = btn.closest('.dcg-lore-row');
          await api('lore', {
            method: 'POST',
            body: JSON.stringify({ lore_id: Number(row.dataset.id), action: 'restore' }),
          });
          loadLore();
        });
      });
    } catch (err) {
      loreList.innerHTML = `<p class="dcg-status-err">${esc(err.message)}</p>`;
    }
  };

  container.querySelector('#dcg-lore-add')?.addEventListener('click', async () => {
    const account = select.value;
    const content = container.querySelector('#dcg-lore-content')?.value?.trim();
    const guildId = loreGuildSelect?.value?.trim() || '';
    const channelId = loreChannelSelect?.value?.trim() || '';
    if (!account || !content) { setStatus('Account and lore content required.'); return; }
    if (channelId && !guildId) {
      setStatus('Pick a server before attaching lore to a channel.');
      return;
    }
    try {
      await api('lore', {
        method: 'POST',
        body: JSON.stringify({
          action: 'add',
          account,
          content,
          guild_id: guildId,
          channel_id: channelId,
        }),
      });
      const input = container.querySelector('#dcg-lore-content');
      if (input) input.value = '';
      await loadLore();
      setStatus(guildId
        ? `Lore added for ${loreScopeLabel(guildId, channelId)}.`
        : 'Lore added (no server scope).');
    } catch (err) {
      setStatus(`Add lore failed: ${err.message}`);
    }
  });
  container.querySelector('#dcg-lore-refresh')?.addEventListener('click', loadLore);
  container.querySelector('#dcg-lore-show-forgotten')?.addEventListener('change', loadLore);
  container.querySelector('#dcg-lore-targets-refresh')?.addEventListener('click', loadLoreTargets);
  loreGuildSelect?.addEventListener('change', () => {
    fillLoreChannelOptions(select.value);
    loadLore();
  });

  const testOut = container.querySelector('#dcg-mem-test-output');
  const runMemoryTest = async (kind, extra = {}) => {
    const account = select.value;
    if (!account) { if (testOut) testOut.textContent = 'Select a bot account first.'; return; }
    const userId = container.querySelector('#dcg-mem-test-user')?.value?.trim() || '';
    const guildId = testGuildSelect?.value?.trim()
      || loreGuildSelect?.value?.trim()
      || 'test-guild';
    if (testOut) testOut.textContent = `Running ${kind}…`;
    try {
      const data = await api('memory/test', {
        method: 'POST',
        body: JSON.stringify({ kind, account, user_id: userId, guild_id: guildId, ...extra }),
      });
      if (testOut) testOut.textContent = JSON.stringify(data, null, 2);
      await loadList();
      await loadLore();
    } catch (err) {
      if (testOut) testOut.textContent = `Test failed: ${err.message}`;
    }
  };
  container.querySelector('#dcg-mem-test-milestone')?.addEventListener('click', () => runMemoryTest('milestone'));
  container.querySelector('#dcg-mem-test-interact')?.addEventListener('click', () => runMemoryTest('simulate_interaction', {
    count: 1,
    gap_days: 21,
    message_text: 'Been playing games and coding a python deploy',
  }));
  container.querySelector('#dcg-mem-test-lore')?.addEventListener('click', () => runMemoryTest('lore', {
    content: 'Deploy day is Thursday',
    pinned: true,
  }));
  container.querySelector('#dcg-mem-test-interest')?.addEventListener('click', () => runMemoryTest('interest', { topic: 'coding' }));
  container.querySelector('#dcg-mem-test-context')?.addEventListener('click', () => runMemoryTest('context_preview'));
  container.querySelector('#dcg-mem-test-distill')?.addEventListener('click', () => runMemoryTest('ambient_distill', {
    seed_text: [
      'I have a dog named Mochi and walk him every morning',
      'I usually work night shifts so I sleep late',
      'Been playing games and coding on python this week',
      'Coffee over tea for me, always',
      'My birthday is in March but we already know that maybe',
      'Deploy day is Thursday on our team — wait that is server lore',
      'I live with two cats as well actually',
      'Prefer short replies in Discord chat',
    ].join('\n'),
  }));

  select.addEventListener('change', () => {
    fillLoreGuildOptions(select.value);
    loadList();
    loadLore();
    loadLoreTargets();
  });
  container.querySelector('#dcg-mem-refresh')?.addEventListener('click', () => {
    loadList();
    loadLore();
    loadLoreTargets();
  });
  if (names.length) {
    loadList();
    loadLore();
    loadLoreTargets();
  } else {
    list.innerHTML = '<p class="dcg-help" style="margin:0">Add a bot account first.</p>';
  }
}

function greetingTargetLabel(value) {
  return greetingTargetCatalog[value]?.label || value;
}

function fieldByData(container, fieldId) {
  return [...container.querySelectorAll('[data-field]')].find((el) => el.dataset.field === fieldId) || null;
}

function checkboxByDataValue(container, pickerId, value) {
  return [...container.querySelectorAll(`#${pickerId} input[type="checkbox"]`)]
    .find((el) => el.dataset.value === value) || null;
}

function syncGreetingTargetsField(container) {
  const hidden = fieldByData(container, 'proactive.greeting_targets');
  if (hidden) {
    hidden.value = [...greetingTargetSelection].sort().join('\n');
  }
}

function renderGreetingTargetChips(container) {
  const box = container.querySelector('#dcg-target-chips');
  if (!box) return;
  box.innerHTML = '';
  if (!greetingTargetSelection.size) {
    box.innerHTML = '<span class="dcg-help">None selected</span>';
    return;
  }
  [...greetingTargetSelection].sort().forEach((value) => {
    const chip = document.createElement('span');
    chip.className = 'dcg-target-chip';
    const text = document.createElement('span');
    text.textContent = greetingTargetLabel(value);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Remove');
    btn.textContent = '×';
    btn.addEventListener('click', () => {
      greetingTargetSelection.delete(value);
      const cb = checkboxByDataValue(container, 'dcg-target-picker', value);
      if (cb) cb.checked = false;
      renderGreetingTargetChips(container);
      syncGreetingTargetsField(container);
    });
    chip.appendChild(text);
    chip.appendChild(btn);
    box.appendChild(chip);
  });
}

function renderGreetingTargetPicker(container, targets) {
  const box = container.querySelector('#dcg-target-picker');
  if (!box) return;
  box.innerHTML = '';
  if (!targets.length) {
    box.innerHTML = '<p class="dcg-help" style="margin:0">No text channels found. Connect a bot and ensure the daemon is running.</p>';
    return;
  }
  const groups = {};
  targets.forEach((target) => {
    greetingTargetCatalog[target.value] = target;
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
      cb.checked = greetingTargetSelection.has(target.value);
      cb.addEventListener('change', () => {
        if (cb.checked) greetingTargetSelection.add(target.value);
        else greetingTargetSelection.delete(target.value);
        renderGreetingTargetChips(container);
        syncGreetingTargetsField(container);
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

async function loadGreetingTargetPicker(container) {
  const status = container.querySelector('#dcg-target-status');
  const refreshBtn = container.querySelector('#dcg-target-refresh');
  if (refreshBtn) refreshBtn.disabled = true;
  if (status) status.textContent = 'Loading…';
  try {
    const data = await api('proactive/targets');
    if (data.error && !data.targets?.length) throw new Error(data.error);
    renderGreetingTargetPicker(container, data.targets || []);
    const count = (data.targets || []).length;
    if (status) {
      status.textContent = count
        ? `${count} channel${count === 1 ? '' : 's'} available`
        : (data.error || 'No connected bots');
    }
    renderGreetingTargetChips(container);
  } catch (err) {
    if (status) status.textContent = err.message || 'Failed to load channels';
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

function initGreetingTargetPicker(container, selectedTargets) {
  greetingTargetSelection = new Set((selectedTargets || []).map((line) => String(line).trim()).filter(Boolean));
  syncGreetingTargetsField(container);
  renderGreetingTargetChips(container);

  container.querySelector('#dcg-target-refresh')?.addEventListener('click', () => loadGreetingTargetPicker(container));
  container.querySelector('#dcg-target-select-all')?.addEventListener('click', () => {
    container.querySelectorAll('#dcg-target-picker input[type="checkbox"]').forEach((cb) => {
      cb.checked = true;
      greetingTargetSelection.add(cb.dataset.value);
    });
    renderGreetingTargetChips(container);
    syncGreetingTargetsField(container);
  });
  container.querySelector('#dcg-target-clear')?.addEventListener('click', () => {
    greetingTargetSelection.clear();
    container.querySelectorAll('#dcg-target-picker input[type="checkbox"]').forEach((cb) => {
      cb.checked = false;
    });
    renderGreetingTargetChips(container);
    syncGreetingTargetsField(container);
  });

  if (container.querySelector('.dcg-notice')?.textContent?.includes('Daemon is running')) {
    loadGreetingTargetPicker(container);
  }
}

function ignoredChannelLabel(value) {
  return ignoredChannelCatalog[value]?.label || greetingTargetCatalog[value]?.label || value;
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
    greetingTargetCatalog[target.value] = greetingTargetCatalog[target.value] || target;
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
    const data = await api('proactive/targets');
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
      data = await api('proactive/targets?channel_type=voice');
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

function syncPresencePresetsField(container) {
  const hidden = fieldByData(container, 'presence.activity_presets');
  if (hidden) {
    hidden.value = [...presencePresetSelection].sort().join('\n');
  }
}

function renderPresencePresetPicker(container, presets, defaultIds) {
  presencePresetDefaults = [...(defaultIds || [])].sort();
  const box = container.querySelector('#dcg-presence-preset-picker');
  if (!box) return;
  box.innerHTML = '';
  if (!presets.length) {
    box.innerHTML = '<p class="dcg-help" style="margin:0">No presets available.</p>';
    return;
  }
  if (!presencePresetSelection.size && defaultIds?.length) {
    defaultIds.forEach((id) => presencePresetSelection.add(id));
    syncPresencePresetsField(container);
  }
  presets.forEach((preset) => {
    presencePresetCatalog[preset.id] = preset;
    const label = document.createElement('label');
    label.className = 'dcg-target-option';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.dataset.presetId = preset.id;
    cb.checked = presencePresetSelection.has(preset.id);
    cb.addEventListener('change', () => {
      if (cb.checked) presencePresetSelection.add(preset.id);
      else presencePresetSelection.delete(preset.id);
      syncPresencePresetsField(container);
    });
    const text = document.createElement('span');
    text.textContent = `${preset.label} (${preset.value || 'clear'})`;
    label.appendChild(cb);
    label.appendChild(text);
    box.appendChild(label);
  });
}

async function loadPresencePresetPicker(container) {
  const box = container.querySelector('#dcg-presence-preset-picker');
  if (!box) return;
  try {
    const data = await api('presence/presets');
    renderPresencePresetPicker(container, data.presets || [], data.default_enabled_ids || []);
  } catch (err) {
    box.innerHTML = `<p class="dcg-help" style="margin:0">${esc(err.message || 'Failed to load presets')}</p>`;
  }
}

function initPresencePresetPicker(container, selectedPresets) {
  presencePresetSelection = new Set((selectedPresets || []).map((line) => String(line).trim()).filter(Boolean));
  syncPresencePresetsField(container);
  loadPresencePresetPicker(container);
}

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
  list.querySelectorAll('.dcg-test-account').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const out = list.querySelector(`.dcg-test-result[data-name="${CSS.escape(btn.dataset.name)}"]`);
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
          const refreshed = await api('accounts');
          renderAccounts(container, refreshed.accounts || []);
          const fresh = container.querySelector(`.dcg-test-result[data-name="${CSS.escape(btn.dataset.name)}"]`);
          if (fresh) fresh.textContent = okText;
          if (result.reconnect) {
            setTimeout(async () => {
              try {
                const again = await api('accounts');
                renderAccounts(container, again.accounts || []);
              } catch (e) { /* next manual refresh will catch up */ }
            }, 4000);
          }
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
        const refreshed = await api('accounts');
        renderAccounts(container, refreshed.accounts || []);
        if (refreshed.accounts?.some((a) => a.connected)) {
          loadGreetingTargetPicker(container);
          loadVoiceTargetPicker(container);
        }
      } catch (e) {
        alert(e.message);
        btn.disabled = false;
      }
    });
  });
}

function formatProactiveDiagnostics(data) {
  if (!data || data.error) {
    const err = data?.error || 'unknown';
    const daemon = data?.daemon_running === false ? ' (daemon offline)' : '';
    return `Diagnostics unavailable: ${err}${daemon}`;
  }
  const lines = [
    `Server time: ${data.server_time}`,
    `Connected accounts: ${(data.connected_accounts || []).join(', ') || '(none)'}`,
    `Greeting targets: ${(data.greeting_targets || []).join(', ') || '(none)'}`,
    '',
    `Morning greeting — enabled=${data.greeting?.enabled}, would_fire_now=${data.greeting?.would_fire_now}, scheduled=${data.greeting?.scheduled_intentions}`,
  ];
  for (const hint of data.greeting?.hints || []) lines.push(`  • ${hint}`);
  lines.push('');
  lines.push(`Goodnight — enabled=${data.goodnight?.enabled}, would_fire_now=${data.goodnight?.would_fire_now}, scheduled=${data.goodnight?.scheduled_intentions}`);
  for (const hint of data.goodnight?.hints || []) lines.push(`  • ${hint}`);
  lines.push('');
  lines.push(`Quiet outreach — enabled=${data.outreach?.enabled}, would_fire_now=${data.outreach?.would_fire_now}, scheduled=${data.outreach?.scheduled_intentions}`);
  for (const hint of data.outreach?.hints || []) lines.push(`  • ${hint}`);
  if (data.channels?.length) {
    lines.push('');
    lines.push('Per-channel sleep state:');
    for (const row of data.channels) {
      const st = row.sleep_state || {};
      lines.push(
        `  ${row.account_name}:${row.channel_id} connected=${row.connected} asleep=${st.is_asleep || 0} goodnight_sent=${st.goodnight_sent || 0}`,
      );
    }
  }
  return lines.join('\n');
}

function formatProactiveTestResult(data) {
  const lines = [formatProactiveDiagnostics(data.diagnostics)];
  lines.push('');
  if (data.error) {
    lines.push(`Test error: ${data.error}`);
    if (data.hint) lines.push(data.hint);
    return lines.join('\n');
  }
  lines.push(`Test: ${data.kind} dry_run=${data.dry_run} queued=${data.sent ?? 0}`);
  for (const row of data.results || []) {
    const preview = row.preview ? ` preview="${row.preview.slice(0, 120)}${row.preview.length > 120 ? '…' : ''}"` : '';
    // 'sent' from the pipeline means "a continuity task ACCEPTED the event" —
    // the LLM runs async and can still fail. Say what's true; the internal
    // status value stays 'sent' (coordinator follow-ups key on it).
    const shown = row.status === 'sent' ? 'queued (LLM runs async — watch the channel)' : (row.status || 'unknown');
    lines.push(`  ${row.account_name}:${row.channel_id} → ${shown}${row.reason ? ` (${row.reason})` : ''}${preview}`);
  }
  lines.push('Note: preview text is the static fallback — the live message is LLM-written and may differ.');
  return lines.join('\n');
}

async function refreshProactiveDiagnostics(container) {
  const output = container.querySelector('#dcg-proactive-test-output');
  if (!output) return;
  output.textContent = 'Loading diagnostics…';
  try {
    const data = await api('proactive/diagnostics');
    output.textContent = formatProactiveDiagnostics(data);
  } catch (err) {
    output.textContent = `Diagnostics failed: ${err.message}`;
  }
}

async function runProactiveTest(container, kind) {
  const output = container.querySelector('#dcg-proactive-test-output');
  const dryRun = !!container.querySelector('#dcg-proactive-dry-run')?.checked;
  if (!output) return;
  output.textContent = `Running ${kind} test…`;
  try {
    const data = await api('proactive/test', {
      method: 'POST',
      body: JSON.stringify({ kind, dry_run: dryRun, reset_sleep_state: true }),
    });
    output.textContent = formatProactiveTestResult(data);
  } catch (err) {
    output.textContent = `Test failed: ${err.message}`;
  }
}

function bindProactiveTestPanel(container) {
  const refreshBtn = container.querySelector('#dcg-refresh-proactive-diag');
  const greetingBtn = container.querySelector('#dcg-test-greeting');
  const goodnightBtn = container.querySelector('#dcg-test-goodnight');
  const outreachBtn = container.querySelector('#dcg-test-outreach');
  if (refreshBtn) refreshBtn.addEventListener('click', () => refreshProactiveDiagnostics(container));
  if (greetingBtn) greetingBtn.addEventListener('click', () => runProactiveTest(container, 'greeting'));
  if (goodnightBtn) goodnightBtn.addEventListener('click', () => runProactiveTest(container, 'goodnight'));
  if (outreachBtn) outreachBtn.addEventListener('click', () => runProactiveTest(container, 'outreach'));
  refreshProactiveDiagnostics(container);
}

function bindAccounts(container) {
  const form = container.querySelector('#dcg-add-form');
  const toggle = container.querySelector('#dcg-add-toggle');
  toggle?.addEventListener('click', () => {
    form.style.display = form.style.display === 'none' ? 'block' : 'none';
  });
  container.querySelector('#dcg-acc-test')?.addEventListener('click', async () => {
    const token = container.querySelector('#dcg-acc-token')?.value?.trim();
    const status = container.querySelector('#dcg-acc-status');
    const btn = container.querySelector('#dcg-acc-test');
    if (!token) {
      status.textContent = 'Paste a token to test.';
      status.className = 'dcg-status dcg-status-err';
      return;
    }
    btn.disabled = true;
    status.textContent = 'Testing token against Discord…';
    status.className = 'dcg-status';
    try {
      const result = await api('accounts/test', { method: 'POST', body: JSON.stringify({ token }) });
      status.textContent = result.success ? `✓ ${result.message}` : `✗ ${result.error || 'Test failed'}`;
      status.className = `dcg-status ${result.success ? 'dcg-status-ok' : 'dcg-status-err'}`;
    } catch (e) {
      status.textContent = `✗ ${e.message}`;
      status.className = 'dcg-status dcg-status-err';
    } finally {
      btn.disabled = false;
    }
  });
  container.querySelector('#dcg-acc-save')?.addEventListener('click', async () => {
    const name = container.querySelector('#dcg-acc-name')?.value?.trim();
    const token = container.querySelector('#dcg-acc-token')?.value?.trim();
    const status = container.querySelector('#dcg-acc-status');
    const btn = container.querySelector('#dcg-acc-save');
    if (!name || !token) {
      status.textContent = 'Name and token required.';
      status.className = 'dcg-status dcg-status-err';
      return;
    }
    btn.disabled = true;
    status.textContent = 'Saving…';
    try {
      await api('accounts', {
        method: 'POST',
        body: JSON.stringify({ account_name: name, token }),
      });
      status.textContent = 'Account saved.';
      status.className = 'dcg-status dcg-status-ok';
      const refreshed = await api('accounts');
      renderAccounts(container, refreshed.accounts || []);
      form.style.display = 'none';
      container.querySelector('#dcg-acc-name').value = '';
      container.querySelector('#dcg-acc-token').value = '';
    } catch (e) {
      status.textContent = e.message;
      status.className = 'dcg-status dcg-status-err';
    } finally {
      btn.disabled = false;
    }
  });
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
  const greeting = readLlmBlockValues(container, 'dcg-greeting-llm');
  const goodnight = readLlmBlockValues(container, 'dcg-goodnight-llm');
  const distill = readLlmBlockValues(container, 'dcg-distill-llm');
  // Voice block was missing here — voice.llm_provider/llm_model never saved
  // from the UI (found during the 2026-08-05 slot conversion). ensure_voice_chat
  // treats ''/'auto' as hands-off, so persisting 'auto' is safe.
  const voice = readLlmBlockValues(container, 'dcg-voice-llm');
  const vision = readLlmBlockValues(container, 'dcg-vision-llm');
  const flat = {
    'cognitive.llm_primary': reply.provider,
    'cognitive.llm_model': reply.model,
    'proactive.greeting_model_provider': greeting.provider,
    'proactive.greeting_model_name': greeting.model,
    'proactive.goodnight_model_provider': goodnight.provider,
    'proactive.goodnight_model_name': goodnight.model,
    'profile.distill_model_provider': distill.provider,
    'profile.distill_model_name': distill.model,
    'voice.llm_provider': voice.provider,
    'voice.llm_model': voice.model,
    'media.vision_llm_provider': vision.provider,
    'media.vision_llm_model': vision.model,
    'reaction.sentiment_backend': container.querySelector('#dcg-sentiment-backend')?.value || 'vader',
    'proactive.greeting_targets': [...greetingTargetSelection].sort(),
    'channel.ignored_channels': [...ignoredChannelSelection].sort(),
    'bot.allowlist_ids': [...botAllowlistSelection].sort(),
    'voice.join_targets': [...voiceTargetSelection].sort(),
    // Selection equal to the shipped defaults stores as [] ("follow
    // defaults") — same sentinel the voice prompt uses. Otherwise a fresh
    // install pins today's default presets forever on first save.
    'presence.activity_presets': (() => {
      const picked = [...presencePresetSelection].sort();
      return picked.join('\n') === presencePresetDefaults.join('\n') ? [] : picked;
    })(),
    'voice.conversation_prompt_template': normalizedVoicePrompt(container),
  };
  applyProactiveLlmInheritance(flat);
  return flat;
}

async function loadPanelData() {
  const [accounts, settings, health, summary, traces, llmDebug, cognitionDebug, plugins, values] = await Promise.allSettled([
    api('accounts'),
    api('settings'), // daemon state + built-in defaults only — values live in core now
    api('health'),
    api('admin/summary'),
    api('traces'),
    api('debug/llm?limit=10'),
    api('debug/cognition'),
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
    cognitionDebug: val(cognitionDebug, null),
  };
}

function registerTab() {
  registerPluginSettings({
    id: PLUGIN_NAME,
    name: 'Discord',
    icon: '🎮',
    helpText: 'Discord bot accounts, conversation behavior, social reactions, delivery quirks, proactive scheduling, safety, media, and voice settings.',

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
      // a failed Media/Voice/Proactive slot mount returns fabricated
      // {provider:'auto'} values that would overwrite explicit pins on save.
      // Residual of the 2026-08-05 wipe-guard class.
      const slotPrefixes = ['dcg-llm', 'dcg-greeting-llm', 'dcg-goodnight-llm', 'dcg-distill-llm', 'dcg-voice-llm', 'dcg-vision-llm'];
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
