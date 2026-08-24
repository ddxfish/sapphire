import { registerPluginSettings } from '/static/shared/plugin-registry.js';

const PLUGIN_NAME = 'discord';
const CSRF = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

const DCG_STYLES = `
.dcg { font-family: inherit; color: var(--text); }
.dcg h4 { margin: 0 0 8px; font-size: 1rem; }
.dcg-section { margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
.dcg-row { display: grid; grid-template-columns: 1fr auto; gap: 12px; align-items: center; padding: 8px 0; }
.dcg-row label { font-weight: 500; }
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
.dcg-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.dcg-tab {
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text);
  border-radius: 6px;
  padding: 6px 12px;
  cursor: pointer;
  font: inherit;
}
.dcg-tab.active { background: var(--accent, #5865f2); color: #fff; border-color: transparent; }
.dcg-panel { display: none; }
.dcg-panel.active { display: block; }
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
.dcg-save-bar { display: flex; align-items: center; gap: 12px; margin-top: 16px; }
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
.dcg-panel-intro {
  padding: 10px 12px; border-radius: 8px; margin-bottom: 14px;
  background: color-mix(in srgb, var(--text-muted) 8%, transparent);
  border: 1px solid var(--border);
  font-size: 0.88em; line-height: 1.45;
}
.dcg-panel-intro strong { font-weight: 600; }
.dcg-panel-intro ul { margin: 6px 0 0 1.1em; padding: 0; }
.dcg-panel-intro li { margin: 4px 0; }
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

function bool(id, checked = false) {
  return `<label class="dcg-row"><span><label for="${id}">${esc(id)}</label></span>
    <input type="checkbox" id="${id}" data-field="${id}" ${checked ? 'checked' : ''}></label>`;
}

function num(id, label, value, min, max, step = 1, help = '') {
  return `<div class="dcg-row">
    <div><label for="${id}">${esc(label)}</label>${help ? `<div class="dcg-help">${esc(help)}</div>` : ''}</div>
    <input class="dcg-input" type="number" id="${id}" data-field="${id}" value="${value}" min="${min}" max="${max}" step="${step}">
  </div>`;
}

function text(id, label, value, help = '', type = 'text') {
  return `<div class="dcg-row">
    <div><label for="${id}">${esc(label)}</label>${help ? `<div class="dcg-help">${esc(help)}</div>` : ''}</div>
    <input class="dcg-input" type="${esc(type)}" id="${id}" data-field="${id}" value="${esc(value)}">
  </div>`;
}

function select(id, label, value, options, help = '') {
  const opts = options.map(([v, t]) => `<option value="${esc(v)}" ${v === value ? 'selected' : ''}>${esc(t)}</option>`).join('');
  return `<div class="dcg-row">
    <div><label for="${id}">${esc(label)}</label>${help ? `<div class="dcg-help">${esc(help)}</div>` : ''}</div>
    <select class="dcg-select" id="${id}" data-field="${id}">${opts}</select>
  </div>`;
}

function textarea(id, label, value, help = '') {
  return `<div style="margin:8px 0">
    <label for="${id}"><strong>${esc(label)}</strong></label>
    ${help ? `<div class="dcg-help">${esc(help)}</div>` : ''}
    <textarea class="dcg-textarea" id="${id}" data-field="${id}">${esc(value)}</textarea>
  </div>`;
}

function checkboxRow(id, label, checked, help = '') {
  return `<div class="dcg-row">
    <div><label for="${id}">${esc(label)}</label>${help ? `<div class="dcg-help">${help}</div>` : ''}</div>
    <input type="checkbox" id="${id}" data-field="${id}" ${checked ? 'checked' : ''}>
  </div>`;
}

function panelIntro(html) {
  return `<div class="dcg-panel-intro">${html}</div>`;
}

function llmProviderBlockHtml({
  prefix,
  label,
  help,
  providerField,
  modelField,
  providerValue = 'auto',
  modelValue = '',
}) {
  const provider = providerValue || 'auto';
  return `
    <div class="dcg-row">
      <div>
        <label for="${prefix}-primary">${esc(label)}</label>
        ${help ? `<div class="dcg-help">${help}</div>` : ''}
      </div>
      <select class="dcg-select" id="${prefix}-primary" data-field="${providerField}">
        <option value="auto" ${provider === 'auto' ? 'selected' : ''}>Auto (continuity task)</option>
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
  const resolved = data.settings?.resolved || {};
  const c = resolved.cognitive || {};
  const ch = resolved.channel || {};
  const b = resolved.bot || {};
  const p = resolved.proactive || {};
  const s = resolved.safety || {};
  const m = resolved.media || {};
  const v = resolved.voice || {};
  const defaults = data.settings?.defaults || {};
  const voicePromptDefault = defaults.voice?.conversation_prompt_template || '';
  const r = resolved.retention || {};
  const pr = resolved.presence || {};
  const pf = resolved.profile || {};
  const rx = resolved.reaction || {};
  const del = resolved.delivery || {};
  const daemon = data.settings?.daemon_running;
  const daemonState = data.settings?.daemon_state || data.health?.state || 'unknown';
  const daemonNote = !daemon
    ? `<div class="dcg-notice">Daemon is offline (${esc(daemonState)}). Enable the plugin under Settings → Plugins, then reload it. You do not add a separate daemon — this plugin starts one automatically when enabled.</div>`
    : `<div class="dcg-notice" style="border-color:var(--success)">Daemon is running (${esc(daemonState)}).</div>`;

  container.innerHTML = `
    <style>${DCG_STYLES}</style>
    <div class="dcg">
      ${daemonNote}

      <div class="dcg-section">
        <h4>Bot Accounts</h4>
        <p class="dcg-help">Create a bot at discord.com/developers, enable <strong>Message Content Intent</strong>, then paste the token.</p>
        <div id="dcg-accounts"></div>
        <div style="margin-top:8px;display:flex;gap:8px">
          <button type="button" class="dcg-btn" id="dcg-add-toggle">+ Add Bot</button>
        </div>
        <div id="dcg-add-form" style="display:none;margin-top:12px">
          <div class="dcg-row"><div><label for="dcg-acc-name">Account name</label></div>
            <input class="dcg-input" id="dcg-acc-name" placeholder="mybot"></div>
          <div class="dcg-row"><div><label for="dcg-acc-token">Bot token</label></div>
            <input class="dcg-input" id="dcg-acc-token" type="password" placeholder="token"></div>
          <button type="button" class="dcg-btn dcg-btn-primary" id="dcg-acc-save">Add Bot</button>
          <div class="dcg-status" id="dcg-acc-status"></div>
        </div>
      </div>

      <nav class="dcg-tabs" id="dcg-tabs">
        <button type="button" class="dcg-tab active" data-tab="cognitive">Cognitive</button>
        <button type="button" class="dcg-tab" data-tab="conversation">Conversation</button>
        <button type="button" class="dcg-tab" data-tab="social">Social</button>
        <button type="button" class="dcg-tab" data-tab="proactive">Proactive</button>
        <button type="button" class="dcg-tab" data-tab="presence">Presence</button>
        <button type="button" class="dcg-tab" data-tab="safety">Safety</button>
        <button type="button" class="dcg-tab" data-tab="media">Media</button>
        <button type="button" class="dcg-tab" data-tab="voice">Voice</button>
        <button type="button" class="dcg-tab" data-tab="retention">Retention</button>
      </nav>

      <div class="dcg-panel active" data-panel="cognitive">
        ${panelIntro(`
          <strong>When does the bot decide to speak?</strong> Two layers work together:
          <strong>Conversation → Reply mode</strong> is the hard gate (e.g. mentions only).
          <strong>Cognitive</strong> is the soft brain on top — it can still say no even when a message is allowed,
          and it shapes proactive behaviour. The world model only <em>records</em> what happened; it does not pick replies.
        `)}
        ${checkboxRow(
          'cognitive.enabled',
          'Cognitive layer enabled',
          c.enabled !== false,
          'On: messages go through the intention engine (goals, mood, attention) before Sapphire is asked to reply. Off: every message that passes Reply mode is sent straight to the LLM with no “should I answer?” step.',
        )}
        ${select('cognitive.mode', 'Cognitive mode', c.mode || 'integrated', [
          ['conservative', 'Conservative — hardest to interrupt'],
          ['integrated', 'Integrated — reply when addressed (recommended)'],
          ['expressive', 'Expressive — may join active channels'],
        ], 'How eagerly the bot speaks after Reply mode allows the message. Does not override Mentions only.')}
        ${llmProviderBlockHtml({
          prefix: 'dcg-llm',
          label: 'Reply LLM',
          help: 'Provider for Discord text and voice replies. Auto uses the continuity task\'s provider.',
          providerField: 'cognitive.llm_primary',
          modelField: 'cognitive.llm_model',
          providerValue: c.llm_primary || 'auto',
          modelValue: c.llm_model || '',
        })}
        ${panelIntro(`
          <strong>Mode details</strong>
          <ul>
            <li><strong>Conservative</strong> — Same reply rules as Integrated, but higher internal thresholds. Slightly less likely to accept edge-case triggers; best for quiet servers.</li>
            <li><strong>Integrated</strong> — Replies when @mentioned or when name match fires (if enabled). Ignores normal chat it was not addressed in. Good default with Mentions only.</li>
            <li><strong>Expressive</strong> — Can chime in without a mention if the channel has been very active (high “attention”). Use only if you want ambient participation.</li>
          </ul>
        `)}
        ${checkboxRow(
          'cognitive.task_follow_up_enabled',
          'Task follow-ups',
          c.task_follow_up_enabled !== false,
          'Scheduler checks the world-model task queue and sends follow-ups when due. Sources: voice session end, reminders (“remind me in 5 minutes”), and future commitments. Uses local date parsing on ingest; LLM for wording when due. Birthday wishes use profiles (see below). Needs a Sapphire discord_message task with auto-reply.',
        )}
        ${checkboxRow(
          'cognitive.commitment_followups_enabled',
          'Remember & follow up commitments',
          c.commitment_followups_enabled !== false,
          'Watch channel messages for future promises (keyword gate + local date parsing), schedule a world-model task, then follow up later. Channel messages only.',
        )}
        ${checkboxRow(
          'profile.birthday_capture_enabled',
          'Learn birthdays on profiles',
          pf.birthday_capture_enabled !== false,
          'When someone clearly says “my birthday is …”, store month/day on their profile (not ambient “birthday” chat about other people).',
        )}
        ${checkboxRow(
          'profile.birthday_followups_enabled',
          'Birthday wishes from profiles',
          pf.birthday_followups_enabled !== false,
          'Each birthday gets a stable random time between greeting hour and spread end hour (default 8pm). One wish per person per year — not all at once.',
        )}
        ${checkboxRow(
          'profile.birthday_bulk_enabled',
          'Bulk birthday message',
          pf.birthday_bulk_enabled !== false,
          'When more than the threshold number of birthdays share the same day in one channel, send one combined message instead of individual wishes.',
        )}
        ${num(
          'profile.birthday_bulk_threshold',
          'Bulk birthday threshold',
          pf.birthday_bulk_threshold ?? 3,
          1,
          20,
          1,
          'Bulk mode activates when a channel has more than this many birthdays today (e.g. 3 → 4 or more triggers one combined post in that channel).',
        )}
        ${checkboxRow(
          'cognitive.reminder_followups_enabled',
          'Reminder requests',
          c.reminder_followups_enabled !== false,
          'When someone asks “remind me in 5 minutes to …”, queue a world-model task and @mention them when it is due. Works in channels and DMs.',
        )}
        ${checkboxRow(
          'cognitive.affect_modulation_enabled',
          'Affect modulation',
          c.affect_modulation_enabled !== false,
          'Mood and relationship scores nudge how hard it is to trigger a reply (e.g. low energy or high irritability raises the bar). Observations still go to the world model either way.',
        )}
      </div>

      <div class="dcg-panel" data-panel="conversation">
        ${panelIntro(`
          <strong>Reply mode</strong> controls which Discord messages are even considered for a reply.
          <strong>Cognitive mode</strong> (other tab) only filters further — it cannot make the bot reply if Reply mode blocks the message.
          With <strong>Mentions only</strong>, the bot should not answer normal channel chat unless someone @mentions it
          (or uses name match below).
        `)}
        ${select('channel.reply_mode', 'Reply mode', ch.reply_mode || 'default', [
          ['default', 'Default — allow messages; cognitive layer filters'],
          ['mentions_only', 'Mentions only — require @mention or name match'],
          ['disabled', 'Disabled — never auto-reply in channels'],
        ], 'First gate before the LLM. Mentions only is the usual choice for public servers.')}
        ${checkboxRow(
          'channel.name_match_enabled',
          'Respond to bot name (soft mention)',
          !!ch.name_match_enabled,
          'Treat saying the bot’s display name in text as a trigger, without @. Counts as a mention for sleep forced-wake. Off recommended if the name appears often in conversation.',
        )}
        ${checkboxRow(
          'channel.name_match_case_sensitive',
          'Case-sensitive name match',
          !!ch.name_match_case_sensitive,
          'If enabled, “leona” will not match “Leona”. Only applies when name match is on.',
        )}
        ${num(
          'channel.batching_seconds',
          'Batch window (seconds)',
          ch.batching_seconds ?? 8,
          1,
          120,
          1,
          'Wait this long after a message (and extend while others type) before replying, so rapid messages become one prompt.',
        )}
        ${checkboxRow(
          'channel.strip_think_tags',
          'Strip thinking tags',
          ch.strip_think_tags !== false,
          'Remove &lt;think&gt; / &lt;redacted_thinking&gt; blocks from model output before posting to Discord.',
        )}
        ${checkboxRow(
          'channel.typing_indicator_enabled',
          'Typing indicator',
          ch.typing_indicator_enabled !== false,
          'Show “typing…” in the channel while the reply is being prepared and sent.',
        )}
        ${checkboxRow(
          'channel.human_pause_enabled',
          'Human pause',
          ch.human_pause_enabled !== false,
          'Short random delay after the model finishes, before typing/sending — feels less instant/robotic.',
        )}
        ${checkboxRow(
          'channel.read_delay_enabled',
          'Read delay',
          ch.read_delay_enabled !== false,
          'Brief pause before starting the reply, as if reading the message first.',
        )}
        <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border)">
          <strong>Other bots</strong>
          <p class="dcg-help">Control when Remmi replies to other bots (e.g. Sapphire for debates). Non-allowlisted bots like hourly status posters are ignored. Sessions open when a human @mentions Remmi and close after silence — no fixed turn limit.</p>
        </div>
        ${checkboxRow(
          'bot.enabled',
          'Enable bot-to-bot replies',
          b.enabled !== false,
          'Master switch. When off, messages from other bots never get replies.',
        )}
        ${select('bot.reply_mode', 'Bot reply mode', b.reply_mode || 'allowlist', [
          ['never', 'Never — ignore all bot messages'],
          ['allowlist', 'Allowlist — only listed bot user IDs'],
          ['mentions_only', 'Mentions only — allowlisted bots must @mention Remmi'],
        ], 'Default allowlist ignores broadcast bots (e.g. speedtest posters) unless their ID is listed.')}
        <div style="margin:12px 0">
          <strong>Allowlisted bots</strong>
          <div class="dcg-help">Bots Remmi may debate or interact with. Loaded from servers your bot is in — enable <strong>Server Members Intent</strong> in the Discord Developer Portal if the list is empty.</div>
          <div id="dcg-bot-allowlist-chips" class="dcg-target-chips"><span class="dcg-help">None selected</span></div>
          <div class="dcg-target-toolbar">
            <button type="button" class="dcg-btn" id="dcg-bot-allowlist-refresh">Refresh from Discord</button>
            <button type="button" class="dcg-btn" id="dcg-bot-allowlist-select-all">Select all</button>
            <button type="button" class="dcg-btn" id="dcg-bot-allowlist-clear">Clear all</button>
            <span class="dcg-help" id="dcg-bot-allowlist-status"></span>
          </div>
          <div id="dcg-bot-allowlist-picker" class="dcg-target-picker">
            <p class="dcg-help" style="margin:0">Connect a bot, open this tab, then click Refresh to load bots.</p>
          </div>
          <textarea class="dcg-textarea" id="bot.allowlist_ids" data-field="bot.allowlist_ids" style="display:none" aria-hidden="true">${esc((b.allowlist_ids || []).join('\n'))}</textarea>
        </div>
        ${num(
          'bot.session_human_window_seconds',
          'Human debate window (seconds)',
          b.session_human_window_seconds ?? 300,
          60,
          1800,
          30,
          'After someone @mentions Remmi, bot-to-bot replies stay possible for this long while engagement continues.',
        )}
        ${num(
          'bot.session_silence_seconds',
          'Session silence timeout (seconds)',
          b.session_silence_seconds ?? 150,
          30,
          900,
          15,
          'Stop the bot debate if no cross-bot engagement for this long.',
        )}
        ${num(
          'bot.session_safety_max_exchanges',
          'Safety cap (exchanges)',
          b.session_safety_max_exchanges ?? 20,
          4,
          50,
          1,
          'Hard backstop against runaway loops — normal debates end from silence long before this.',
        )}
      </div>

      <div class="dcg-panel" data-panel="social">
        ${panelIntro(`
          <strong>Social texture</strong> — lurker reactions and human-like delivery quirks.
          These run entirely inside this plugin (no Leona Discord dependency).
          Silent reactions are <strong>off during sleep hours</strong> when the sleep schedule is enabled
          (from sleep hour until morning greeting hour); they run while the bot is awake.
          Install <code>vaderSentiment</code> for better reaction emoji picks on casual Discord text.
        `)}
        <h4 style="margin-top:0">Silent reactions</h4>
        ${checkboxRow(
          'reaction.enabled',
          'Reactions enabled',
          rx.enabled !== false,
          'Master switch for autonomous sentiment reactions and LLM [react:…] tags on replies.',
        )}
        ${checkboxRow(
          'reaction.silent_enabled',
          'Autonomous silent reactions',
          rx.silent_enabled !== false,
          'React without replying when the bot sees a message but chooses not to speak (or while also replying, if enabled below).',
        )}
        ${num(
          'reaction.reaction_chance',
          'Reaction chance (%)',
          rx.reaction_chance ?? 50,
          0,
          100,
          1,
          'Chance to add a sentiment-matched emoji when a message is processed. 0 = never, 100 = always (still subject to cooldown).',
        )}
        ${num(
          'reaction.reaction_cooldown_seconds',
          'Reaction cooldown (seconds)',
          rx.reaction_cooldown_seconds ?? 30,
          0,
          600,
          5,
          'Minimum time between reactions in the same channel. 0 = no cooldown.',
        )}
        ${checkboxRow(
          'reaction.react_on_reply_path',
          'React on reply path',
          rx.react_on_reply_path !== false,
          'May still add a silent reaction when the bot is also about to reply (in addition to any LLM [react:…] tag).',
        )}
        ${checkboxRow(
          'reaction.read_only_enabled',
          'Read-only reactions',
          rx.read_only_enabled !== false,
          'When the bot skips a reply (~5% roll), occasionally react anyway — “saw it, didn’t answer”.',
        )}
        <div style="margin-top:20px;padding-top:12px;border-top:1px solid var(--border)">
          <h4 style="margin-top:0">Human delivery</h4>
          <p class="dcg-help">Typos, self-corrections, and quote-reply style. Skips auto-typos when the user’s message contains <strong>?</strong>. The model can also use <code>[edit:corrected text]</code> in its reply.</p>
        </div>
        ${checkboxRow(
          'delivery.message_edits_enabled',
          'Message edits',
          del.message_edits_enabled !== false,
          'Allow post-send edits (auto typos, [edit:…] tags, and occasional trailing-thought edits).',
        )}
        ${checkboxRow(
          'delivery.auto_typo_enabled',
          'Auto typos',
          !!del.auto_typo_enabled,
          'Sometimes send a realistic misspelling, then correct it after a short pause. Uses a built-in 700+ word list in the plugin (not editable here).',
        )}
        ${num(
          'delivery.auto_typo_chance',
          'Auto typo chance (%)',
          del.auto_typo_chance ?? 12,
          0,
          100,
          1,
          'Only applies when Auto typos is on. LLM [edit:…] always takes priority.',
        )}
        ${num(
          'delivery.auto_typo_delay_min',
          'Typo fix delay min (seconds)',
          del.auto_typo_delay_min ?? 2,
          0.5,
          120,
          0.5,
          'Minimum wait before correcting an auto typo.',
        )}
        ${num(
          'delivery.auto_typo_delay_max',
          'Typo fix delay max (seconds)',
          del.auto_typo_delay_max ?? 6,
          0.5,
          120,
          0.5,
          'Maximum wait before correcting an auto typo.',
        )}
        ${checkboxRow(
          'delivery.quote_reply_enabled',
          'Smart quote-replies',
          del.quote_reply_enabled !== false,
          'Sometimes reply as a Discord quote; skips for jokes, media triggers, and busy channels.',
        )}
        ${checkboxRow(
          'delivery.post_send_edit_enabled',
          'Random post-send edits',
          del.post_send_edit_enabled !== false,
          'Legacy ~4% chance of a subtle typo fix or trailing thought (e.g. “ lol”) when auto typo did not fire.',
        )}
      </div>

      <div class="dcg-panel" data-panel="proactive">
        ${panelIntro('Scheduled and ambient behaviour: greetings, “channel went quiet” outreach, sleep/goodnight, and @mention buffering while asleep. Uses <strong>server local time</strong> for hours.')}
        ${checkboxRow(
          'proactive.greeting_enabled',
          'Morning greetings',
          !!p.greeting_enabled,
          'Post a good-morning message once per day in selected channels at the greeting hour.',
        )}
        ${num(
          'proactive.greeting_utc_hour',
          'Greeting hour (server local)',
          p.greeting_utc_hour ?? 9,
          0,
          23,
          1,
          'Hour (0–23) on the Sapphire server clock when morning greetings may fire. Also ends the overnight sleep window.',
        )}
        ${num(
          'proactive.birthday_wish_spread_end_hour',
          'Birthday wish spread end hour',
          p.birthday_wish_spread_end_hour ?? 20,
          0,
          23,
          1,
          'Birthday wishes are spread randomly from greeting hour until this hour (server local). Avoids blasting every birthday at once.',
        )}
        <div style="margin:12px 0">
          <strong>Greeting channels</strong>
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
          <textarea class="dcg-textarea" id="proactive.greeting_targets" data-field="proactive.greeting_targets" style="display:none" aria-hidden="true">${esc((p.greeting_targets || []).join('\n'))}</textarea>
        </div>
        ${checkboxRow(
          'proactive.greeting_use_llm',
          'AI-generated greeting',
          p.greeting_use_llm !== false,
          'When enabled, Sapphire writes a fresh good-morning message each day. Disable to post a fixed template instead.',
        )}
        ${textarea(
          'proactive.greeting_message',
          'Greeting instructions',
          p.greeting_message || '',
          'Optional LLM instructions for morning greetings. Leave blank for the default warm good-morning prompt.',
        )}
        ${text('proactive.greeting_fallback', 'Greeting fallback', p.greeting_fallback || 'Good morning!', 'Used when AI generation is off or fails.')}
        ${llmProviderBlockHtml({
          prefix: 'dcg-greeting-llm',
          label: 'Greeting model provider',
          help: 'LLM for morning greetings. Defaults to Reply LLM when unset.',
          providerField: 'proactive.greeting_model_provider',
          modelField: 'proactive.greeting_model_name',
          providerValue: p.greeting_model_provider || c.llm_primary || 'auto',
          modelValue: p.greeting_model_name || c.llm_model || '',
        })}
        ${num('proactive.greeting_max_tokens', 'Greeting max tokens', p.greeting_max_tokens ?? 180, 40, 500, 1, 'Token budget for generated greetings.')}
        ${checkboxRow(
          'proactive.outreach_enabled',
          'Quiet outreach',
          !!p.outreach_enabled,
          'If a selected channel has had no messages for a while, send a conversation starter. Suppressed during sleep hours and while asleep.',
        )}
        ${num(
          'proactive.outreach_stale_minutes',
          'Outreach after quiet (minutes)',
          p.outreach_stale_minutes ?? 120,
          15,
          1440,
          1,
          'How long a channel must be silent before outreach is considered.',
        )}
        ${checkboxRow(
          'proactive.sleep_schedule_enabled',
          'Sleep schedule',
          !!p.sleep_schedule_enabled,
          'Overnight behaviour: goodnight message, sleep presence, no outreach/voice auto-join, buffer @mentions until enough pings wake the bot.',
        )}
        ${num(
          'proactive.sleep_utc_hour',
          'Sleep hour (server local)',
          p.sleep_utc_hour ?? 22,
          0,
          23,
          1,
          'Hour when the sleep window starts (until greeting hour). Goodnight is sent around this hour in greeting channels.',
        )}
        ${checkboxRow(
          'proactive.goodnight_use_llm',
          'AI-generated goodnight',
          p.goodnight_use_llm !== false,
          'When enabled, Sapphire writes a fresh good-night message when entering sleep. Disable to post a fixed template instead.',
        )}
        ${textarea(
          'proactive.goodnight_message',
          'Goodnight instructions',
          p.goodnight_message || '',
          'Optional LLM instructions for goodnight messages. Leave blank for the default warm good-night prompt.',
        )}
        ${text('proactive.goodnight_fallback', 'Goodnight fallback', p.goodnight_fallback || 'Goodnight everyone!', 'Used when AI generation is off or fails.')}
        ${llmProviderBlockHtml({
          prefix: 'dcg-goodnight-llm',
          label: 'Goodnight model provider',
          help: 'LLM for goodnight messages. Defaults to Greeting provider, then Reply LLM.',
          providerField: 'proactive.goodnight_model_provider',
          modelField: 'proactive.goodnight_model_name',
          providerValue: p.goodnight_model_provider || p.greeting_model_provider || c.llm_primary || 'auto',
          modelValue: p.goodnight_model_name || p.greeting_model_name || c.llm_model || '',
        })}
        ${num('proactive.goodnight_max_tokens', 'Goodnight max tokens', p.goodnight_max_tokens ?? 180, 40, 500, 1, 'Token budget for generated goodnight messages.')}
        ${num(
          'proactive.forced_wake_mention_threshold',
          'Forced-wake mentions',
          p.forced_wake_mention_threshold ?? 2,
          1,
          10,
          1,
          'How many @mentions in a sleeping channel before the bot wakes and replies (first N−1 are buffered silently).',
        )}
        ${num(
          'proactive.forced_wake_minutes',
          'Forced-wake window (minutes)',
          p.forced_wake_minutes ?? 30,
          5,
          180,
          1,
          'After forced wake, the bot stays “up” this long and may grumble in replies before returning to sleep.',
        )}
        <div class="dcg-proactive-test" style="margin:16px 0;padding:12px;border:1px solid var(--border);border-radius:8px">
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

      <div class="dcg-panel" data-panel="presence">
        ${panelIntro('Discord profile status and “Playing/Listening…” activity. Chosen by the presence service from these settings + sleep/quiet hours — not by the world model. Reload plugin after changing presets file on disk.')}
        ${select('presence.status', 'Awake status', pr.status || 'online', [
          ['online', 'Online'],
          ['idle', 'Idle'],
          ['dnd', 'Do not disturb'],
          ['invisible', 'Invisible'],
        ], 'Discord status while awake (non-cycling)')}
        ${text('presence.activity', 'Default activity', pr.activity || '', 'Static activity when cycling is off. Prefixes: playing:, watching:, listening:, custom:')}
        ${select('presence.quiet_status', 'Quiet / sleep status', pr.quiet_status || 'idle', [
          ['online', 'Online'],
          ['idle', 'Idle'],
          ['dnd', 'Do not disturb'],
          ['invisible', 'Invisible'],
        ], 'Used during quiet hours and sleep')}
        ${text('presence.sleep_activity', 'Sleep activity fallback', pr.sleep_activity || 'custom: sleeping', 'Used when sleep.json pool is empty')}
        ${checkboxRow('presence.cycling_enabled', 'Cycle activities', !!pr.cycling_enabled, 'Randomly rotate through enabled presets while awake')}
        ${num('presence.cycle_interval_seconds', 'Cycle interval (seconds)', pr.cycle_interval_seconds ?? 300, 60, 86400, 60, 'Minimum time between presence changes while awake')}
        <div style="margin:12px 0">
          <strong>Activity presets</strong>
          <div class="dcg-help">Checked presets are included in the rotation pool when cycling is enabled.</div>
          <div id="dcg-presence-preset-picker" class="dcg-target-picker">
            <p class="dcg-help" style="margin:0">Loading presets…</p>
          </div>
          <textarea class="dcg-textarea" id="presence.activity_presets" data-field="presence.activity_presets" style="display:none" aria-hidden="true">${esc((pr.activity_presets || []).join('\n'))}</textarea>
        </div>
        ${textarea('presence.activities_custom', 'Custom activities (one per line)', (pr.activities_custom || []).join('\n'), 'Optional extra rotation entries. Use playing:, watching:, listening:, or custom: prefixes.')}
      </div>

      <div class="dcg-panel" data-panel="safety">
        ${panelIntro('Rate limits and quiet hours. Quiet hours affect presence (idle) and proactive outreach — not whether @mention replies are allowed.')}
        ${checkboxRow(
          'safety.allow_direct_messages',
          'Allow DMs',
          s.allow_direct_messages !== false,
          'If off, DM observations are dropped by policy. DMs can still use cognitive rules when allowed.',
        )}
        ${num(
          'safety.rate_limit_seconds',
          'Reply cooldown (seconds)',
          s.rate_limit_seconds ?? 30,
          0,
          600,
          1,
          'Minimum time between bot replies in the same channel (0 = no limit). Applies after a reply is approved.',
        )}
        ${num(
          'safety.proactive_cooldown_hours',
          'Proactive cooldown (hours)',
          s.proactive_cooldown_hours ?? 6,
          1,
          48,
          1,
          'Minimum gap between proactive actions (greeting, outreach, etc.) per channel.',
        )}
        ${checkboxRow(
          'safety.quiet_hours_enabled',
          'Quiet hours',
          !!s.quiet_hours_enabled,
          'During this window the bot shows quiet presence and skips proactive outreach. Does not block @mentions.',
        )}
        ${num(
          'safety.quiet_hours_start',
          'Quiet start (server local hour)',
          s.quiet_hours_start ?? 0,
          0,
          23,
          1,
          'Start of quiet hours (0–23). Can overlap sleep schedule; both apply their own effects.',
        )}
        ${num(
          'safety.quiet_hours_end',
          'Quiet end (server local hour)',
          s.quiet_hours_end ?? 0,
          0,
          23,
          1,
          'End of quiet hours. If start equals end, quiet hours are disabled.',
        )}
      </div>

      <div class="dcg-panel" data-panel="media">
        ${panelIntro('GIF/meme/image pipelines. GIF search needs an API key. Auto-GIF chance is per-reply probability when the model emits a GIF tag.')}
        ${checkboxRow('media.enabled', 'Media pipeline', !!m.enabled)}
        ${checkboxRow(
          'media.image_understanding_enabled',
          'Image understanding',
          !!m.image_understanding_enabled,
          'Allow the plugin to interpret image and GIF attachments when media processing is enabled.',
        )}
        ${select('media.vision_provider', 'Vision provider', m.vision_provider || 'openai_compat', [
          ['openai_compat', 'OpenAI-compatible'],
          ['openai', 'OpenAI'],
          ['openrouter', 'OpenRouter'],
          ['custom', 'Custom'],
        ], 'Which vision API shape to use for image understanding requests.')}
        ${text(
          'media.vision_base_url',
          'Vision base URL',
          m.vision_base_url || '',
          'Optional override for the vision API endpoint root.',
        )}
        ${text(
          'media.vision_model',
          'Vision model',
          m.vision_model || '',
          'Model name used for image understanding requests.',
        )}
        ${text(
          'media.vision_api_key',
          'Vision API key',
          m.vision_api_key || '',
          'Secret used for the configured vision provider.',
          'password',
        )}
        ${num(
          'media.vision_timeout_seconds',
          'Vision timeout (seconds)',
          m.vision_timeout_seconds ?? 30,
          1,
          300,
          1,
          'Maximum time to wait for a vision response before falling back.',
        )}
        ${select('media.vision_gif_mode', 'Vision GIF mode', m.vision_gif_mode || 'first_frame', [
          ['first_frame', 'First frame'],
          ['sampled_frames', 'Sampled frames'],
        ], 'How GIF attachments should be summarized when image understanding is enabled.')}
        ${checkboxRow(
          'media.vision_debug_enabled',
          'Vision debug logging',
          !!m.vision_debug_enabled,
          'When enabled, emit provider detection plus request start/success/failure details to plugin traces and daemon logs.',
        )}
        ${checkboxRow('media.gif_enabled', 'GIF replies', !!m.gif_enabled, 'Search and send GIFs via Klipy/Giphy/Tenor')}
        ${text('media.gif_api_key', 'GIF API key', m.gif_api_key || '', 'Klipy: partner.klipy.com — also works for Tenor-style keys')}
        ${select('media.gif_provider', 'GIF provider', m.gif_provider || 'klipy', [
          ['klipy', 'Klipy (recommended)'],
          ['giphy', 'Giphy'],
          ['tenor', 'Tenor (legacy)'],
        ])}
        ${select('media.gif_content_filter', 'GIF content filter', m.gif_content_filter || 'medium', [
          ['off', 'Off'],
          ['low', 'Low'],
          ['medium', 'Medium'],
          ['high', 'High'],
        ])}
        ${num('media.gif_auto_chance', 'Auto-GIF chance (0–1)', m.gif_auto_chance ?? 0, 0, 1, 0.05)}
        ${num('media.gif_cooldown_seconds', 'GIF cooldown (seconds)', m.gif_cooldown_seconds ?? 300, 0, 3600, 30)}
        ${checkboxRow('media.meme_enabled', 'Meme responses', !!m.meme_enabled)}
      </div>

      <div class="dcg-panel" data-panel="voice">
        ${panelIntro('Voice join/leave, transcription, and TTS. Auto-join polls selected voice channels; disabled while the bot is in sleep mode.')}
        ${checkboxRow('voice.enabled', 'Voice enabled', !!v.enabled)}
        ${select('voice.mode', 'Voice mode', v.mode || 'listen_only', [
          ['listen_only', 'Listen only'],
          ['transcribe_only', 'Transcribe only'],
          ['summarize_only', 'Summarize only'],
          ['conversational', 'Conversational'],
        ])}
        ${checkboxRow('voice.transcription_enabled', 'Transcription', !!v.transcription_enabled)}
        ${checkboxRow('voice.speaking_enabled', 'Speaking', !!v.speaking_enabled)}
        ${checkboxRow('voice.emergency_disabled', 'Emergency disable voice', !!v.emergency_disabled)}
        <div style="margin:12px 0">
          <strong>Auto-join voice channels</strong>
          <div class="dcg-help">Voice channels to auto-join when someone is in them, and leave when empty. Polled every ~15s while the daemon runs.</div>
          <div id="dcg-voice-target-chips" class="dcg-target-chips"><span class="dcg-help">None selected</span></div>
          <div class="dcg-target-toolbar">
            <button type="button" class="dcg-btn" id="dcg-voice-target-refresh">Refresh from Discord</button>
            <button type="button" class="dcg-btn" id="dcg-voice-target-select-all">Select all</button>
            <button type="button" class="dcg-btn" id="dcg-voice-target-clear">Clear all</button>
            <span class="dcg-help" id="dcg-voice-target-status"></span>
          </div>
          <div id="dcg-voice-target-picker" class="dcg-target-picker">
            <p class="dcg-help" style="margin:0">Connect a bot, open this tab, then click Refresh to load voice channels.</p>
          </div>
          <textarea class="dcg-textarea" id="voice.join_targets" data-field="voice.join_targets" style="display:none" aria-hidden="true">${esc((v.join_targets || []).join('\n'))}</textarea>
        </div>
        ${textarea(
          'voice.conversation_prompt_template',
          'Voice conversation prompt',
          v.conversation_prompt_template || voicePromptDefault,
          'System instructions for conversational voice mode. Placeholders: {primary} = bot name, {alias_line} = alias suffix (empty when none). Clear and save to restore the built-in default.',
        )}
      </div>

      <div class="dcg-panel" data-panel="retention">
        ${panelIntro('How long SQLite keeps messages, traces, and voice transcripts. Run purge from Operator debug / admin API to apply immediately.')}
        ${checkboxRow(
          'retention.enabled',
          'Retention jobs enabled',
          r.enabled !== false,
          'When on, scheduled cleanup can trim old rows according to the day limits below.',
        )}
        ${num('retention.message_days', 'Keep messages (days)', r.message_days ?? 90, 1, 3650)}
        ${num('retention.trace_days', 'Keep traces (days)', r.trace_days ?? 14, 1, 365)}
        ${num('retention.transcript_days', 'Keep transcripts (days)', r.transcript_days ?? 30, 1, 3650)}
      </div>

      <div class="dcg-save-bar">
        <button type="button" class="dcg-btn dcg-btn-primary" id="dcg-save">Save Settings</button>
        <span class="dcg-status" id="dcg-save-status"></span>
      </div>

      <details class="dcg-details" style="margin-top:20px">
        <summary>Operator debug</summary>
        <pre id="dcg-debug" style="font-size:0.8em;overflow:auto;max-height:240px"></pre>
      </details>
    </div>
  `;

  renderAccounts(container, data.accounts?.accounts || []);
  container.querySelector('#dcg-debug').textContent = JSON.stringify({
    health: data.health,
    summary: data.summary,
    trace_count: (data.traces?.traces || []).length,
  }, null, 2);

  bindTabs(container);
  bindProactiveTestPanel(container);
  const voicePromptField = fieldByData(container, 'voice.conversation_prompt_template');
  if (voicePromptField && voicePromptDefault) {
    voicePromptField.dataset.defaultTemplate = voicePromptDefault;
  }
  bindAccounts(container);
  initGreetingTargetPicker(container, p.greeting_targets || []);
  initBotAllowlistPicker(container, b.allowlist_ids || []);
  initVoiceTargetPicker(container, v.join_targets || []);
  initPresencePresetPicker(container, pr.activity_presets || []);
  initLlmProviderBlocks(container, {
    reply: {
      prefix: 'dcg-llm',
      providerKey: c.llm_primary || 'auto',
      modelName: c.llm_model || '',
    },
    greeting: {
      prefix: 'dcg-greeting-llm',
      providerKey: p.greeting_model_provider || c.llm_primary || 'auto',
      modelName: p.greeting_model_name || c.llm_model || '',
      inheritsReply: !p.greeting_model_provider,
    },
    goodnight: {
      prefix: 'dcg-goodnight-llm',
      providerKey: p.goodnight_model_provider || p.greeting_model_provider || c.llm_primary || 'auto',
      modelName: p.goodnight_model_name || p.greeting_model_name || c.llm_model || '',
      inheritsGreeting: !p.goodnight_model_provider,
    },
  });
  container.querySelector('#dcg-save')?.addEventListener('click', () => saveSettings(container));
}

let _LLM_PROVIDERS = [];
let _LLM_METADATA = {};
const _llmInheritState = {
  greetingInheritsReply: false,
  goodnightInheritsGreeting: false,
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

function llmProviderOptionsHtml(selected = 'auto') {
  const options = [`<option value="auto" ${selected === 'auto' ? 'selected' : ''}>Auto (continuity task)</option>`];
  for (const provider of _LLM_PROVIDERS) {
    const key = provider.key || provider.display_name || '';
    if (!key) continue;
    const label = provider.display_name || key;
    options.push(`<option value="${esc(key)}" ${key === selected ? 'selected' : ''}>${esc(label)}</option>`);
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
}

function initLlmProviderBlock(container, { prefix, providerKey, modelName, onProviderChange }) {
  const els = llmBlockElements(container, prefix);
  if (!els.primary) return;

  const applyProvider = (key, model) => {
    els.primary.innerHTML = llmProviderOptionsHtml(key || 'auto');
    if (key && key !== 'auto') {
      els.primary.value = key;
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
}

function syncAllLlmModelFields(container) {
  syncLlmModelField(container, 'dcg-llm');
  syncLlmModelField(container, 'dcg-greeting-llm');
  syncLlmModelField(container, 'dcg-goodnight-llm');
}

function applyProactiveLlmInheritance(settings) {
  const cognitive = settings.cognitive || {};
  const proactive = settings.proactive || {};
  const replyProvider = cognitive.llm_primary || 'auto';
  const replyModel = cognitive.llm_model || '';

  if ((proactive.greeting_model_provider || 'auto') === replyProvider
    && (proactive.greeting_model_name || '') === replyModel) {
    proactive.greeting_model_provider = '';
    proactive.greeting_model_name = '';
  }

  const greetingProvider = proactive.greeting_model_provider || replyProvider;
  const greetingModel = proactive.greeting_model_name || replyModel;
  if ((proactive.goodnight_model_provider || greetingProvider) === greetingProvider
    && (proactive.goodnight_model_name || greetingModel) === greetingModel) {
    proactive.goodnight_model_provider = '';
    proactive.goodnight_model_name = '';
  }

  settings.cognitive = cognitive;
  settings.proactive = proactive;
}

const greetingTargetCatalog = {};
let greetingTargetSelection = new Set();
const presencePresetCatalog = {};
let presencePresetSelection = new Set();

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

  container.querySelector('[data-tab="proactive"]')?.addEventListener('click', () => {
    const picker = container.querySelector('#dcg-target-picker');
    if (picker && !picker.querySelector('.dcg-target-group') && !picker.querySelector('.dcg-help[data-loaded]')) {
      loadGreetingTargetPicker(container);
    }
  });

  if (container.querySelector('.dcg-notice')?.textContent?.includes('Daemon is running')) {
    loadGreetingTargetPicker(container);
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

  container.querySelector('[data-tab="conversation"]')?.addEventListener('click', () => {
    const picker = container.querySelector('#dcg-bot-allowlist-picker');
    if (picker && !picker.querySelector('.dcg-target-group') && !picker.querySelector('.dcg-help[data-loaded]')) {
      loadBotAllowlistPicker(container);
    }
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

  container.querySelector('[data-tab="voice"]')?.addEventListener('click', () => {
    const picker = container.querySelector('#dcg-voice-target-picker');
    if (picker && !picker.querySelector('.dcg-target-group') && !picker.querySelector('.dcg-help[data-loaded]')) {
      loadVoiceTargetPicker(container);
    }
  });
}

function syncPresencePresetsField(container) {
  const hidden = fieldByData(container, 'presence.activity_presets');
  if (hidden) {
    hidden.value = [...presencePresetSelection].sort().join('\n');
  }
}

function renderPresencePresetPicker(container, presets, defaultIds) {
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
  container.querySelector('[data-tab="presence"]')?.addEventListener('click', () => {
    const picker = container.querySelector('#dcg-presence-preset-picker');
    if (picker && !picker.querySelector('input[type="checkbox"]') && picker.textContent.includes('Loading')) {
      loadPresencePresetPicker(container);
    }
  });
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
        <span class="dcg-badge ${a.connected ? 'dcg-badge-ok' : 'dcg-badge-off'}">${a.connected ? 'connected' : a.state || 'disconnected'}</span>
        <div class="dcg-help">${esc(a.name)}${a.last_error ? ` — ${esc(a.last_error)}` : ''}</div>
      </div>
      <button type="button" class="dcg-btn dcg-btn-danger dcg-del-account" data-name="${esc(a.name)}">Remove</button>
    </div>
  `).join('');
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
  lines.push(`Test: ${data.kind} dry_run=${data.dry_run} sent=${data.sent ?? 0}`);
  for (const row of data.results || []) {
    const preview = row.preview ? ` preview="${row.preview.slice(0, 120)}${row.preview.length > 120 ? '…' : ''}"` : '';
    lines.push(`  ${row.account_name}:${row.channel_id} → ${row.status || 'unknown'}${row.reason ? ` (${row.reason})` : ''}${preview}`);
  }
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

function bindTabs(container) {
  container.querySelectorAll('.dcg-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      container.querySelectorAll('.dcg-tab').forEach((t) => t.classList.toggle('active', t === tab));
      container.querySelectorAll('.dcg-panel').forEach((p) => {
        p.classList.toggle('active', p.dataset.panel === tab.dataset.tab);
      });
    });
  });
}

function bindAccounts(container) {
  const form = container.querySelector('#dcg-add-form');
  const toggle = container.querySelector('#dcg-add-toggle');
  toggle?.addEventListener('click', () => {
    form.style.display = form.style.display === 'none' ? 'block' : 'none';
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

function normalizeVoicePromptSettings(settings, container) {
  const voice = settings.voice || {};
  const field = fieldByData(container, 'voice.conversation_prompt_template');
  const defaultTemplate = field?.dataset.defaultTemplate || '';
  if (defaultTemplate && (voice.conversation_prompt_template || '').trim() === defaultTemplate.trim()) {
    voice.conversation_prompt_template = '';
  }
  settings.voice = voice;
}

function readOverlay(container) {
  const overlay = {};
  container.querySelectorAll('[data-field]').forEach((el) => {
    const path = el.dataset.field.split('.');
    let section = overlay;
    for (let i = 0; i < path.length - 1; i += 1) {
      section[path[i]] = section[path[i]] || {};
      section = section[path[i]];
    }
    const key = path[path.length - 1];
    if (el.type === 'checkbox') {
      section[key] = el.checked;
    } else if (el.type === 'number') {
      section[key] = Number(el.value);
    } else if (el.tagName === 'TEXTAREA' && key === 'greeting_targets') {
      section[key] = el.value.split('\n').map((l) => l.trim()).filter(Boolean);
    } else if (el.tagName === 'TEXTAREA' && key === 'join_targets') {
      section[key] = el.value.split('\n').map((l) => l.trim()).filter(Boolean);
    } else if (el.tagName === 'TEXTAREA' && key === 'activity_presets') {
      section[key] = el.value.split('\n').map((l) => l.trim()).filter(Boolean);
    } else if (el.tagName === 'TEXTAREA' && key === 'activities_custom') {
      section[key] = el.value.split('\n').map((l) => l.trim()).filter(Boolean);
    } else if (el.tagName === 'TEXTAREA' && key === 'allowlist_ids') {
      section[key] = el.value.split('\n').map((l) => l.trim()).filter(Boolean);
    } else {
      section[key] = el.value;
    }
  });
  return overlay;
}

async function saveSettings(container) {
  const status = container.querySelector('#dcg-save-status');
  const btn = container.querySelector('#dcg-save');
  syncGreetingTargetsField(container);
  syncBotAllowlistField(container);
  syncVoiceTargetsField(container);
  syncPresencePresetsField(container);
  syncAllLlmModelFields(container);
  const settings = readOverlay(container);
  normalizeVoicePromptSettings(settings, container);
  applyProactiveLlmInheritance(settings);
  btn.disabled = true;
  status.textContent = 'Saving…';
  status.className = 'dcg-status';
  try {
    await api('settings', {
      method: 'POST',
      body: JSON.stringify({ scope_type: 'global', settings }),
    });
    status.textContent = 'Saved.';
    status.className = 'dcg-status dcg-status-ok';
  } catch (e) {
    status.textContent = e.message;
    status.className = 'dcg-status dcg-status-err';
  } finally {
    btn.disabled = false;
  }
}

async function loadPanelData() {
  const [accounts, settings, health, summary, traces] = await Promise.allSettled([
    api('accounts'),
    api('settings'),
    api('health'),
    api('admin/summary'),
    api('traces'),
  ]);
  const val = (r, fallback = {}) => (r.status === 'fulfilled' ? r.value : fallback);
  const settingsData = val(settings, {});
  const healthData = val(health, {});
  const daemonRunning = settingsData.daemon_running === true
    || healthData.daemon_running === true
    || healthData.state === 'ready'
    || healthData.state === 'starting';
  return {
    accounts: val(accounts, { accounts: [] }),
    settings: {
      ...settingsData,
      resolved: settingsData.resolved || {},
      defaults: settingsData.defaults || {},
      daemon_running: daemonRunning,
      daemon_state: settingsData.daemon_state || healthData.state || 'unknown',
    },
    health: healthData,
    summary: val(summary, {}),
    traces: val(traces, { traces: [] }),
  };
}

function registerTab() {
  registerPluginSettings({
    id: PLUGIN_NAME,
    name: 'Discord Cognitive',
    icon: '\uD83C\uDFAE',
    helpText: 'Discord bot accounts, cognitive behavior, social reactions, delivery quirks, proactive scheduling, safety, media, and voice settings.',

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
      syncGreetingTargetsField(container);
      syncVoiceTargetsField(container);
      syncAllLlmModelFields(container);
      const settings = readOverlay(container);
      applyProactiveLlmInheritance(settings);
      return settings;
    },

    save: async (settings) => {
      await api('settings', {
        method: 'POST',
        body: JSON.stringify({ scope_type: 'global', settings }),
      });
    },
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
