// Game Room — the story room: the story ADAPTER on the room host (F6,
// 2026-09-09; Plan B origin tmp/story-primitive-plan.md). Hosts a story on
// the Surface in `rail` mode: the REAL chat rail + composer, borrowed via the
// organ transplant, IS the stage, floating over the per-room backdrop.
// Streaming, mic, TTS, stop, images all work because they're the same live
// nodes, just visiting.
//
// Playthroughs are mode-tagged chats (game_id "story:<slug>") — the chat IS
// the save. The host (room-host.js) owns the chrome, the activate + supersede
// dance, the session picker, the sections, the vault/reconnect watch, the
// backdrop claim order and the 12s liveness tick. This file owns what is
// STORY: start/resume, the storyteller identity block, the status panel,
// seals, the lightbox, tap-to-draft cards and the character screen.

import { accordionHtml } from '/static/shared/accordion.js';
import { coreSections } from '/static/surface/sections/core-sections.js';
import { getInitData } from '/static/shared/init-data.js';
import * as coreApi from '/static/api.js';
import * as ui from '/static/ui.js';
import { on as busOn, Events as BusEvents } from '/static/core/event-bus.js';

// The referee's module name in the core registry — the "include story tools"
// checkbox unions it into ANY toolset via the chat's extra_toolsets setting.
const STORY_TOOLS = 'plugin_game-room_story_tools';
// Sections the story room hand-rolls: prompt (identity dropdown owns it)
// and toolset (needs the story-tools checkbox).
const OWN_SECTIONS = ['prompt', 'toolset'];

let host = null;                 // ./room-host.js — same URL as index.js's import,
                                 // so the module cache returns the same instance
const bootV = () => document.querySelector('meta[name="boot-version"]')?.content || '';

let _ctx = null;                 // the host ctx while a story room is open
let _root = null, _story = null, _session = null, _back = null, _stories = [];
let _chatSettings = {};          // the host's live settings object (same reference)
let _status = null;              // last story/status payload (.active)
let _houseWasOpen = null;        // zork-line transition detector (null = no baseline)
let _liveShown = {};             // seal key → live popup already raised this wait

// Every story call names its session (finding 1.1) — the host's api does.
const api = (path, method, body) => _ctx.api(path, method, body);

// Live seal wait nudge (Krem 2026-08-06): the server publishes tool_executing
// the instant story_act begins — if she's reaching for an unfilled seal, a
// wait registers within milliseconds. Poll now (and once more past the
// registration race) so the popup raises the moment she reaches, not up to
// 12s later. The host's tick carries every liveness rule of the 12s poll.
busOn(BusEvents.TOOL_EXECUTING, (d) => {
    const c = _ctx;
    if (!c || document.hidden) return;
    if (!d || d.name !== 'story_act') return;
    c.tickNow();
    setTimeout(() => { if (_ctx === c) c.tickNow(); }, 1200);
});

// Start a story, routing through the Mad-Libs setup form when the story
// declares slots/presets/sets (open-mansion v1). Returns true = started,
// false = server refused, null = player cancelled the form.
async function startWithSetup(slug) {
    const mod = await import(`./settings-modal.js?v=${bootV()}`);
    const setup = await mod.storyNeedsSetup(slug);
    let payload = { story: slug };
    let sealedFills = [];
    if (setup) {
        // _session rides along so the form's Environment tab can stock the
        // house pre-start (rows land on this chat's user layer; start merges).
        const choice = await mod.openStorySetup(slug, setup, _session);
        if (choice === null) return null;
        payload = { story: slug, slots: choice.slots };
        sealedFills = choice.sealed || [];
    }
    const r = await api('story/start', 'POST', payload);
    if (!r.success) { ui.showToast(r.detail || 'story start failed', 'error'); return false; }
    // Sealed slot values ride the seal machinery — written at turn 0,
    // never through her (the whole mechanic).
    for (const f of sealedFills) {
        try {
            const fr = await api('story/fill', 'POST', { key: f.key, text: f.text });
            if (!fr.success) ui.showToast(`Seal '${f.key}': ${fr.detail}`, 'warning', 4000);
        } catch (e) { ui.showToast(`Seal '${f.key}': ${e.message}`, 'warning', 4000); }
    }
    return true;
}

export async function openStoryRoom(root, story, sessionName, opts) {
    if (!host) host = await import(`./room-host.js?v=${bootV()}`);
    host.close();                              // a previous room's onClose runs NOW, not under our state
    const stories = opts?.stories || [];
    const bySlug = Object.fromEntries(stories.map(s => [s.slug, s]));
    const slugOf = (c) => String(c?.settings?.game_id || '').startsWith('story:')
        ? String(c.settings.game_id).slice(6) : null;

    _root = root; _story = story; _session = sessionName;
    _back = opts?.back; _stories = stories;
    _houseWasOpen = null;                      // fresh baseline per room entry
    _status = null; _lastActive = null;

    const spec = {
        id: 'story:' + story.slug, kind: 'story',
        title: story.title || story.slug, icon: '\u{1F4D6}',
        hash: '#app-game-room/story:' + encodeURIComponent(story.slug),
        loading: 'Opening the book...',
        stage: { mode: 'rail' },
        ownSections: OWN_SECTIONS,
        bareSections: false,                   // the Storyteller accordion places them
        sidebarTop: panelHtml,
        sessions: {
            match: (c) => !!bySlug[slugOf(c)],
            label: (c) => `\u{1F4D6} ${host.esc(c.display_name || c.name)}`,
            open: (name, c) => {
                const st = (c && bySlug[slugOf(c)]) || story;
                openStoryRoom(root, st, name, opts);
            },
        },
        newSession: {
            noun: 'playthrough',
            defaultName: () => `${story.slug} ${new Date().toISOString().slice(0, 10)}`,
        },
        deleteSession: {
            confirm: (name) => `Delete playthrough "${name}"?\n\nRemoves its chat history and its place in "${story.title || story.slug}".`,
            // End first: restores the prompt and closes the active.json entry —
            // a deleted chat must not leave a ghost story that an identically-
            // named future chat would resume into.
            before: (ctx) => ctx.api('story/end', 'POST', {}),
        },
        preOpen, onOpen, onClose,
        // After a reboot the role prompt ('rose') may be unregistered — and
        // core's missing-prompt fallback rewrites the chat to 'default'
        // (Sapph-not-Rose, 2026-08-05). Re-assert re-dresses the costume.
        onReconnect: (ctx) => ctx.api('story/reassert', 'POST', {}).catch(() => {}),
        tick: { every: 12000, fn: tickFn },
    };
    await host.openRoom(root, spec, sessionName, opts);
}

// index.js compat: the host owns the organs; this just forwards.
export function close() { host?.close(); }

// Between activate and skeleton. Resume-aware start: auto-start ONLY a
// virgin session (zero messages). A session with history but no active
// story is an ENDED playthrough — refreshing the room must not silently
// begin a new tale (and re-stamp the cockpit). The ▶ Start button is the
// deliberate way back in. Returns false when the player cancels the setup.
async function preOpen(ctx) {
    _ctx = ctx;
    _chatSettings = ctx.settings;
    const story = _story, session = _session;
    try {
        const status = await api('story/status');
        if (!ctx.live()) return false;
        if (!(status.active && status.active.slug === story.slug)) {
            const sess = (await host.listSessions()).find(c => c.name === session);
            if (!ctx.live()) return false;
            if (!sess || !sess.message_count) {
                // Mad-Libs gate (open-mansion v1): a story that declares
                // slots/presets/sets gets its setup form BEFORE room 1;
                // plain stories keep the zero-friction auto-start.
                const started = await startWithSetup(story.slug);
                if (!ctx.live()) return false;
                if (started === null) return false;        // player cancelled
                if (!started) throw new Error('story start failed');
            }
        } else {
            await api('story/reassert', 'POST').catch(() => {});
        }
        const st2 = await api('story/status');
        if (!ctx.live()) return false;
        _status = st2.active;
        // Ended playthrough: THE END survives refresh — the journal replay
        // (status.last) recovers the final scene + earned ending card.
        if (!_status && st2.last && st2.last.slug === story.slug) _lastActive = st2.last;
        // Re-snapshot settings AFTER story_start stamped prompt/toolset/extras —
        // the pre-start snapshot painted the story-tools checkbox unchecked on
        // every fresh start while the server was actually armed (2026-08-03).
        const fresh = await coreApi.getChatSettings(session).catch(() => null);
        if (!ctx.live()) return false;
        if (fresh && fresh.settings) { ctx.setSettings(fresh.settings); _chatSettings = fresh.settings; }
    } catch (e) {
        ui.showToast(e.message, 'error');
        _status = null;                      // room still works — the rail is real
    }
    return true;
}

async function onOpen(ctx) {
    _ctx = ctx;
    _chatSettings = ctx.settings;
    _root.querySelector('#st-preview').onclick = previewModal;
    paintPanel();
    await initPromptBlock();
    await initToolsetBlock();
}

function onClose() {
    _ctx = null;
    _root = null; _story = null; _session = null; _status = null;
    _hintShown = {}; _lastActive = null; _sealPrompted = {}; _sealHeld = {};
    _liveShown = {}; _shownSeq = null;
    document.getElementById('st-lightbox')?.remove();
}

// The 12s poll body (the host guards liveness; ctx.live() is the token).
// Story ended by ANY path (her story_end tool included): end() may have
// restored or kept the cockpit — resync the settings-backed UI so the
// sidebar never lies (get_self_info-at-none desync, 2026-08-03).
async function tickFn(ctx) {
    const s = await api('story/status');
    if (!ctx.live()) return;
    const wasActive = !!_status;
    _status = s.active;
    if (!_status && s.last && _story && s.last.slug === _story.slug && !_lastActive) _lastActive = s.last;
    if (wasActive && !_status) await resyncSettingsUI();
    if (!ctx.live()) return;
    paintPanel();
}

// ------------------------------------------------------------------ sidebar html
// The story's face (status + controls) and the Storyteller accordion —
// identity dropdown, return prompt, toolset + story-tools checkbox, and the
// remaining bare core sections (brain) placed INSIDE it.

function panelHtml() {
    return `
            <div class="sidebar-section">
                <div class="st-status" id="st-status"></div>
                <div class="st-ctl-row" id="st-ctl-row"></div>
            </div>
            ${accordionHtml({
                id: 'story:teller', title: 'Storyteller', icon: '\u{1F3AD}',
                content: `
                <div class="sb-field">
                    <label>prompt</label>
                    <div class="sb-field-row">
                        <select id="st-mode"></select>
                        <button type="button" id="st-preview" class="sb-btn-sm" title="Preview the full prompt — exactly what she gets">&#x1F441;</button>
                    </div>
                </div>
                <div class="sb-field" id="st-local-group" style="display:none">
                    <label>local prompt</label>
                    <select id="st-local"></select>
                </div>
                <label class="st-tools-check">
                    <input type="checkbox" id="st-return-check"> return to a specific prompt after
                </label>
                <div class="sb-field" id="st-return-group" style="display:none">
                    <label>after the story</label>
                    <select id="st-return" title="Which prompt returns when the story ends"></select>
                </div>
                <div class="sb-field">
                    <label>toolset</label>
                    <select id="st-toolset"></select>
                </div>
                <label class="st-tools-check">
                    <input type="checkbox" id="st-story-tools"> include story tools
                </label>
                ${coreSections.filter(s => s.bare && !OWN_SECTIONS.includes(s.key)).map(s =>
                    `<div class="gs-content" data-sec="${s.key}"></div>`).join('')}`,
            })}`;
}

// ------------------------------------------------------------------ identity

// The three-mode dropdown (Krem's ruling 2026-08-03): Story = total swap
// into the pack's role; Local = a local prompt inside the story as
// themselves; Combined = local persona wearing the role as a mask.
async function initPromptBlock() {
    const root = _root, session = _session;
    const modeSel = root.querySelector('#st-mode');
    const localSel = root.querySelector('#st-local');
    const localGroup = root.querySelector('#st-local-group');
    const esc = host.esc;

    const hasRole = !!(_story.role);
    const roleLabel = hasRole ? `Story — ${_story.role}` : 'Story (no role in this pack)';
    modeSel.innerHTML = `
        <option value="story"${hasRole ? '' : ' disabled'}>${esc(roleLabel)}</option>
        <option value="local">Local prompt</option>
        <option value="combined"${hasRole ? '' : ' disabled'}>Combined${hasRole ? ` — as ${esc(_story.role)}` : ''}</option>`;

    const init = await getInitData().catch(() => null);
    if (_root !== root || _session !== session) return;
    const prompts = init?.prompts?.list || [];
    const opts = prompts.map(p =>
        `<option value="${esc(p.name)}">${esc(p.name.charAt(0).toUpperCase() + p.name.slice(1))}</option>`).join('');
    localSel.innerHTML = opts;

    // "Return to a specific prompt after" — checkbox reveals the select;
    // unchecked = default behavior (whatever the chat wore pre-story).
    const returnSel = root.querySelector('#st-return');
    const returnCheck = root.querySelector('#st-return-check');
    const returnGroup = root.querySelector('#st-return-group');
    returnSel.innerHTML = opts;
    const explicit = !!(_status && _status.return_prompt);
    returnCheck.checked = explicit;
    returnGroup.style.display = explicit ? '' : 'none';
    const retVal = (_status && (_status.return_prompt || _status.prev_prompt)) || '';
    if (retVal) returnSel.value = retVal;
    const postReturn = async (val) => {
        try {
            const r = await api('story/mode', 'POST', { return_prompt: val });
            if (!r.success) throw new Error(r.detail || 'save failed');
            ui.showToast(r.detail, 'success', 3000);
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
    returnCheck.addEventListener('change', () => {
        returnGroup.style.display = returnCheck.checked ? '' : 'none';
        postReturn(returnCheck.checked ? returnSel.value : '');
    });
    returnSel.addEventListener('change', () => postReturn(returnSel.value));

    const active = () => !!(_status && _status.slug === _story.slug);
    const paint = () => {
        // No running story (ended, or awaiting ▶ Start): identity modes are
        // meaningless, but the PROMPT must stay steerable — she may still be
        // wearing Rose (stay-as-role ending) and the user talks to her here.
        const on = active();
        modeSel.disabled = !on;
        modeSel.title = on ? '' : 'identity modes apply to a running story';
        const lbl = localGroup.querySelector('label');
        if (on) {
            const mode = _status.mode || (hasRole ? 'story' : 'local');
            modeSel.value = mode;
            localGroup.style.display = (mode === 'local' || mode === 'combined') ? '' : 'none';
            if (lbl) lbl.textContent = 'local prompt';
            const local = _status.local || _chatSettings.prompt || '';
            if (local) localSel.value = local;
        } else {
            localGroup.style.display = '';
            if (lbl) lbl.textContent = 'prompt';
            const cur = _chatSettings.prompt || '';
            if (cur && [...localSel.options].some(o => o.value === cur)) localSel.value = cur;
        }
    };
    paint();
    _paintPrompt = paint;   // paintPanel re-syncs this block on status changes

    const postMode = async () => {
        if (!active()) {
            // ended/idle: the select IS the chat's prompt switcher
            try {
                await coreApi.updateChatSettings(session, { prompt: localSel.value });
                _chatSettings.prompt = localSel.value;
                ui.showToast(`Prompt: ${localSel.value}`, 'success', 2500);
            } catch (e) { ui.showToast(e.message, 'error'); }
            return;
        }
        try {
            const body = { mode: modeSel.value };
            if (modeSel.value !== 'story') body.local = localSel.value;
            const r = await api('story/mode', 'POST', body);
            if (!r.success) throw new Error(r.detail || 'mode change failed');
            _status = (await api('story/status')).active;
            paint(); paintPanel();
            ui.showToast(r.detail, 'success', 3500);
        } catch (e) {
            ui.showToast(e.message, 'error');
            paint();
        }
    };
    modeSel.addEventListener('change', postMode);
    localSel.addEventListener('change', postMode);
}

// Toolset dropdown is NORMAL (like chat) with the story-tools checkbox as an
// independent axis — phone + story tools = she can call you mid-ARG.
async function initToolsetBlock() {
    const root = _root, session = _session;
    const sel = root.querySelector('#st-toolset');
    const check = root.querySelector('#st-story-tools');
    const esc = host.esc;

    const init = await getInitData().catch(() => null);
    if (_root !== root || _session !== session) return;
    const sets = (init?.toolsets?.list || []).filter(t => t.type !== 'module');
    sel.innerHTML = sets.map(t =>
        `<option value="${esc(t.name)}">${esc(t.name)} (${t.function_count})</option>`).join('');
    sel.value = _chatSettings.toolset || 'none';
    if (sel.value !== (_chatSettings.toolset || 'none')) sel.value = 'none';
    check.checked = (_chatSettings.extra_toolsets || []).includes(STORY_TOOLS);

    const save = async (patch) => {
        try {
            await coreApi.updateChatSettings(session, patch);
            Object.assign(_chatSettings, patch);
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
    sel.addEventListener('change', () => save({ toolset: sel.value }));
    check.addEventListener('change', () => {
        const extras = (_chatSettings.extra_toolsets || []).filter(x => x !== STORY_TOOLS);
        if (check.checked) extras.push(STORY_TOOLS);
        save({ extra_toolsets: extras });
        if (!check.checked) ui.showToast('Referee off — she can narrate but nothing mechanical can advance.', 'warning', 5000);
    });
}

// The critical feature (Krem 2026-08-03): EXACTLY what she gets. Since
// 2026-08-23 this is core's own assembly (/api/chats/{chat}/prompt-preview):
// persona + custom context + spice + EVERY plugin prompt_inject (surface-
// filtered — avatar, etc.) and the real ghost envelope (story state block
// rides inside it). The old client-side stitch silently omitted plugin
// injections — Krem caught avatar instructions she was getting unseen.
async function previewModal() {
    let pp, data;
    try {
        pp = await import(`./prompt-preview.js?v=${bootV()}`);
        data = await pp.fetchPromptPreview(_session);
    } catch (e) { ui.showToast(e.message, 'error'); return; }
    document.getElementById('st-modal')?.remove();
    const wrap = document.createElement('div');
    wrap.id = 'st-modal';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:10000';
    wrap.innerHTML = `
        <div style="background:var(--bg-primary,#0f1020);color:var(--text,#e1e1e6);padding:20px 24px;border-radius:10px;width:min(860px,92vw);max-height:86vh;overflow:auto;border:1px solid var(--border,#333)">
            <h3 style="margin:0 0 4px">&#x1F441; What she gets — verbatim</h3>
            ${pp.promptPreviewHtml(data)}
            <div style="display:flex;justify-content:flex-end">
                <button class="btn-sm" id="st-modal-close">Close</button>
            </div>
        </div>`;
    wrap.addEventListener('click', e => { if (e.target === wrap) wrap.remove(); });
    wrap.querySelector('#st-modal-close').onclick = () => wrap.remove();
    document.body.appendChild(wrap);
}

// Refetch the session's settings and repaint everything that mirrors them
// (toolset select + story-tools checkbox; the prompt block repaints via
// paintPanel). Called on the active→ended transition from any code path.
async function resyncSettingsUI() {
    // Capture at entry: switching playthroughs during the fetch must not
    // write the OLD session's settings into the NEW room (4.10's cousin).
    const root = _root, sess = _session;
    if (!root || !sess) return;
    try {
        const r = await fetch(`/api/chats/${encodeURIComponent(sess)}/settings`,
            { headers: { 'X-CSRF-Token': csrf() } });
        if (!r.ok) return;
        const body = await r.json().catch(() => null);
        if (_root !== root || _session !== sess) return;   // superseded
        _chatSettings = (body && body.settings) || _chatSettings;
        _ctx?.setSettings(_chatSettings);      // the host's sections read the same object
        const tsel = _root.querySelector('#st-toolset');
        if (tsel) tsel.value = _chatSettings.toolset || 'none';
        const chk = _root.querySelector('#st-story-tools');
        if (chk) chk.checked = (_chatSettings.extra_toolsets || []).includes(STORY_TOOLS);
    } catch (e) { /* next poll retries */ }
}

// ------------------------------------------------------------------ story panel
// Port of web/sidebar.js (the 📖 chat accordion) into the room's sidebar.

function paintPanel() {
    const a = _status;
    _ctx?.stageInfo(a ? host.esc(`turn ${a.turn}${a.paused ? ' · paused' : a.ended ? ' · ended' : ''}`) : '');
    // Zork-line moment (open-mansion v1): toast ONCE when the house opens
    // live in this session. Baseline null on entry — resuming an already
    // open house never re-toasts.
    if (a && a.open_flag) {
        if (_houseWasOpen === false && a.house_open) {
            ui.showToast('\u{1F3E0} The house is open — the Rooms panel is live. '
                + 'Stock rooms, load a set, be the ghost. (⚙ → Rooms)', 'success', 8000);
        }
        _houseWasOpen = !!a.house_open;
    }
    paintStatus(a);
    paintControls(a);
    paintBackdrop(a);
    if (_paintPrompt) _paintPrompt();
    checkSeals(a);
    checkShown(a);
}

// The controls line, icon-only: ⏸/▶ · ⏹ · 🔍 · ⚙ (▶ Start when idle).
// Re-rendered per paint, so bindings live here — including the gear.
function paintControls(a) {
    const row = _root?.querySelector('#st-ctl-row');
    if (!row) return;
    const gearHtml = `<button class="btn-sm" id="st-gm-settings" title="Story settings — setup, story text, objects, GM style">&#x2699;&#xFE0E;</button>`;
    const bindGear = () => {
        row.querySelector('#st-gm-settings').onclick = async () => {
            const mod = await import(`./settings-modal.js?v=${bootV()}`);
            // In-game gear: the full modal family — Setup (this run) +
            // Objects tabs join when a playthrough is live (open-mansion v1).
            mod.openStorySettings(_story?.slug, {
                session: _session,
                active: !!(_status && _status.slug === _story?.slug),
                slots: _status?.slots || {},
                fence: _status?.fence || [],
                state: _status || null,   // read-only State tab (the old 🔍)
                onEndStory: endStory,
                storyTitle: _story?.title || _story?.slug,
            });
        };
    };
    if (!a || a.slug !== _story.slug) {
        row.innerHTML = `<button class="btn-sm st-ctl-wide" id="st-start">&#x25B6; Start Story</button>${gearHtml}`;
        bindGear();
        row.querySelector('#st-start').onclick = async () => {
            try {
                const started = await startWithSetup(_story.slug);
                if (started === null) return;              // player cancelled
                if (!started) throw new Error('start failed');
                _status = (await api('story/status')).active;
                paintPanel();
            } catch (e) { ui.showToast(e.message, 'error'); }
        };
        return;
    }
    row.innerHTML = `
        <button class="btn-sm" id="st-pause" title="${a.paused ? 'Resume — back into the story costume' : 'Pause — intermission; chat returns to your persona (Game Room sidebar setting)'}">${a.paused ? '&#x25B6;' : '&#x23F8;'}</button>
        ${gearHtml}`;
    bindGear();
    row.querySelector('#st-pause').onclick = async () => {
        try {
            const r = await api('story/pause', 'POST', { paused: !a.paused });
            if (r.detail) ui.showToast(r.detail, 'success', 3500);
            _status = (await api('story/status')).active;
            await resyncSettingsUI();   // pause may have swapped the persona
            paintPanel();
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
}

// End the playthrough — lives in the gear's State tab now (Krem 2026-08-21:
// the toolbar keeps only ⏸ and ⚙; ending is a deliberate act, not a button
// next to pause). The gear passes this as opts.onEndStory.
async function endStory() {
    try {
        const r = await api('story/end', 'POST');
        if (r.detail) ui.showToast(r.detail, 'success', 5000);
        const s = await api('story/status');
        _status = s.active;
        if (!_status && s.last && s.last.slug === _story.slug) _lastActive = s.last;
        await resyncSettingsUI();
        paintPanel();
    } catch (e) { ui.showToast(e.message, 'error'); }
}

// Player-facing scene strip — the establishing shot the transcript alone
// doesn't give. Her detailed version rides the ghost block; this is yours:
// title, short desc, exit chips, visible interactables (🧩 = unsolved puzzle,
// ✓ = solved), and turn-gated hints behind a 💡 click — never auto-spoiled.
let _hintShown = {};   // room_id → revealed hint index (survives 12s repaints)
let _sealPrompted = {}; // seal key → auto-popup already shown once
let _sealHeld = {};     // seal key → last seen held count (bump = she reached early)
let _lastActive = null; // last non-null status — lets THE END keep its scene
let _paintPrompt = null; // initPromptBlock's paint(), re-run on status changes

// Per-room backdrop, painted straight onto the transplanted #chatbg by the
// host (claim/restore order + DOM-truth assertion live there). Story ended:
// the finale art lingers — and if the outcome has a dedicated ending card,
// the STAGE wears it too (the credits shot).
function paintBackdrop(a) {
    if (!_ctx) return;
    if (!a) {
        const card = _lastActive && _lastActive.ending_card;
        if (card) _ctx.backdrop(card);
        return;
    }
    _ctx.backdrop(a.room_backdrop || null);
}

// The sidebar status panel — the story's face (Krem's layout 2026-08-03):
// room image, title, short desc, exits, here-chips (🧩 unsolved / ✓ solved),
// carrying, mood + numeric stats (trust, HP...), turn-gated 💡 hints behind
// a click. Replaces both the old accordion rows and the stage-top strip.
function paintStatus(a) {
    const box = _root?.querySelector('#st-status');
    if (!box) return;
    const esc = host.esc;
    if (a) _lastActive = a;
    // The tale closed (story_end) but we're still in the room: the finale
    // panel LINGERS — that art and that room are the whole payoff.
    if (!a && _lastActive) {
        const art = _lastActive.ending_card || _lastActive.room_backdrop;
        const img = art ? `<div class="st-status-img"><img src="${esc(art)}" alt=""></div>` : '';
        box.innerHTML = `${img}
            <div class="st-scene-title">&#x1F4D6; The End — ${esc(_lastActive.story || '')}</div>
            <div class="st-scene-desc">The tale closed at ${esc(_lastActive.room || 'its final scene')}. The journal is sealed; her own prompt is back. Start again for another telling.</div>`;
        box.style.display = '';
        return;
    }
    if (!a) {
        box.innerHTML = `<div class="st-scene-desc" style="color:var(--text-muted)">No active story in this playthrough.</div>`;
        box.style.display = '';
        return;
    }
    const exits = (a.room_exits || []).map((x, i) =>
        `<span class="st-exit st-tap" data-exit="${i}" title="${esc(x.desc)}">${esc(x.label)}</span>`).join('');
    // Section = emoji heading + badge row (Krem 2026-08-21). The door moved
    // from every badge onto the heading — compact badges, one glyph up top.
    const sect = (icon, label, body) => body
        ? `<div class="st-scene-h">${icon} ${label}</div><div class="st-scene-exits">${body}</div>` : '';
    // Icon language (Krem 2026-08-03): ✓ done-something, 🧩 unsolved puzzle,
    // ✋ has do-verbs, 👁 look-only. One glyph carries the affordance.
    const objIcon = o => o.solved || o.used ? '✓ '
        : o.puzzle ? '\u{1F9E9} '
        : (o.verbs || []).some(v => v !== 'look' && v !== 'solve') ? '✋ '
        : '\u{1F441} ';
    const objs = (a.room_objects || []).map((o, i) =>
        `<span class="st-exit st-obj st-tap" data-obj="${i}" title="${esc(o.desc)}">${objIcon(o)}${esc(o.name.replace(/[_-]/g, ' '))}</span>`).join('');
    const carrying = (a.inventory || []).map(i =>
        `<span class="st-exit st-obj">${esc(String(i).replace(/[_-]/g, ' '))}</span>`).join('');
    const stats = [];
    if ((a.emotions || []).length) stats.push(`mood: ${a.emotions.join(', ')}`);
    for (const [k, v] of Object.entries(a.flags || {})) {
        if (typeof v === 'number') stats.push(`${k.replace(/_/g, ' ')}: ${v}`);
    }
    // Sealed blanks: ✍ open (the story is waiting on your words), ✉ filled
    // (sealed — editable until she finds it). Revealed ones leave the row.
    const sealChips = (a.seals || []).map((s, i) => ({ s, i })).filter(x => !x.s.revealed);
    const sealsRow = sealChips.map(x => {
        const open = !x.s.filled && !x.s.skipped;
        return `<span class="st-exit st-obj st-tap" data-seal="${x.i}" title="${esc(open
            ? 'the story left this blank for you — tap to write it'
            : 'sealed — tap to edit before she finds it')}">${open ? '✍' : '✉'} ${esc(pretty(x.s.object))}</span>`;
    }).join('');
    // People (Krem 2026-08-25, party frames): square avatar tiles, 3 per
    // row (33% width so a face never eats the sidebar's vertical budget),
    // name below — WoW-party style. NPCs/vendors land here for free.
    const people = Object.entries(a.cast || {}).map(([cid, c]) => {
        const face = c.badge
            ? `<img src="${esc(c.badge)}" alt="" style="width:100%;aspect-ratio:1;object-fit:cover;border-radius:8px;display:block">`
            : `<div style="width:100%;aspect-ratio:1;display:flex;align-items:center;justify-content:center;font-size:1.8em;background:var(--bg-tertiary,#2a2a33);border-radius:8px">\u{1F464}</div>`;
        return `<div class="st-tap" data-person="${esc(cid)}" title="${esc((c.desc || '').slice(0, 140))}" style="cursor:pointer;text-align:center;min-width:0">${face}<div style="font-size:.78em;margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(pretty(c.name || cid))}</div></div>`;
    }).join('');
    const hints = a.player_hints || [];
    const shownIdx = _hintShown[a.room_id];
    const shown = (shownIdx != null && hints.length) ? hints[Math.min(shownIdx, hints.length - 1)] : null;
    const hintBtn = hints.length && shown == null
        ? `<button type="button" class="st-hint-btn" id="st-hint-btn" title="A nudge, not an answer">\u{1F4A1} hint</button>` : '';
    box.innerHTML = `
        ${a.room_backdrop ? `<div class="st-status-img"><img src="${esc(a.room_backdrop)}" alt=""></div>` : ''}
        <div class="st-scene-title">${esc(a.room)}</div>
        ${a.room_desc ? `<div class="st-scene-desc">${esc(a.room_desc)}</div>` : ''}
        ${sect('\u{1F6AA}', 'Exits', exits)}
        ${sect('\u{1F4CD}', 'Here', objs)}
        ${sect('\u{1F392}', 'Carrying', carrying)}
        ${people ? `<div class="st-scene-h">\u{1F465} People</div><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:4px 0">${people}</div>` : ''}
        ${stats.length ? `<div class="st-scene-exits">${esc(stats.join(' · '))}</div>` : ''}
        ${(a.blockers || []).length ? `<div class="st-scene-exits" style="color:var(--text-muted)">${a.blockers.map(b => {
            const i = b.indexOf(' — ');
            const who = i > 0 ? b.slice(0, i) : b;
            const why = i > 0 ? b.slice(i + 3) : '';
            return `<span${why ? ` title="${esc(why)}"` : ''}>\u{1F512} ${esc(who)}</span>`;
        }).join(' · ')}</div>` : ''}
        ${sealsRow ? `<div class="st-scene-exits">${sealsRow}</div>` : ''}
        ${hintBtn ? `<div class="st-scene-exits">${hintBtn}</div>` : ''}
        ${shown != null ? `<div class="st-hint">\u{1F4A1} ${esc(shown)}${hints.length > 1 && shownIdx < hints.length - 1
            ? ` <button type="button" class="st-hint-btn" id="st-hint-btn" title="Another nudge">more</button>` : ''}</div>` : ''}`;
    box.style.display = '';
    const btn = box.querySelector('#st-hint-btn');
    if (btn) btn.onclick = () => {
        // gentlest hint first; "more" escalates toward the strongest
        _hintShown[a.room_id] = shownIdx == null ? 0 : Math.min(shownIdx + 1, hints.length - 1);
        paintStatus(a);
    };
    // Tap-to-draft: chips open a card (desc + declared verbs); tapping a
    // verb pre-fills the REAL composer — the player edits, flavors, sends.
    // Never auto-sends: the voice stays theirs, she narrates, the referee rules.
    box.querySelectorAll('[data-obj]').forEach(el => el.onclick = () => {
        const o = (a.room_objects || [])[+el.dataset.obj];
        if (o) sceneCard(pretty(o.name), o.desc,
            (o.verbs || []).map(v => ({ label: v, draft: draftVerb(v, o.name) })));
    });
    box.querySelectorAll('[data-exit]').forEach(el => el.onclick = () => {
        const x = (a.room_exits || [])[+el.dataset.exit];
        if (x) sceneCard(x.label, x.desc, [{ label: 'go', draft: `Let's go ${x.label}.` }]);
    });
    box.querySelectorAll('[data-seal]').forEach(el => el.onclick = () => {
        const s = (a.seals || [])[+el.dataset.seal];
        if (s) sealModal(s);
    });
    box.querySelectorAll('[data-person]').forEach(el => el.onclick = () => {
        const c = (a.cast || {})[el.dataset.person];
        if (c) personCard(el.dataset.person, c, a);
    });
}

// ------------------------------------------------------------------ sealed blanks
// (Krem 2026-08-04) The author leaves a reveal BLANK; the PLAYER writes it
// mid-run in this popup; she discovers it verbatim through the referee. The
// text never passes through her on the way in — the player surprises the
// storyteller. Popup auto-raises on room entry, and again with urgency if
// she reaches for the object before the blank is filled (held-count bump).

function checkSeals(a) {
    const seals = (a && a.seals) || [];
    // Live wait (Krem 2026-08-06): she is blocked inside story_act RIGHT NOW,
    // waiting for this seal. Raise the popup with a countdown, bypassing the
    // prompted-once suppression — but only once per wait (_liveShown), so a
    // deliberate "Later" isn't fought every tick. A modal already open for
    // the same key is upgraded in place (countdown injected), never recreated
    // — recreating would eat the player's typing mid-sentence.
    const live = (a && a.seal_wait) || null;   // {key, remaining}
    const openModal = document.getElementById('st-seal-modal');
    if (live) {
        if (openModal && openModal.dataset.key === live.key) {
            syncCountdown(openModal, live.remaining);
        } else if (!openModal && !_liveShown[live.key]) {
            const s = seals.find(x => x.key === live.key && !x.revealed);
            if (s) {
                _liveShown[live.key] = true;
                _sealHeld[s.key] = s.held || 0;
                _sealPrompted[s.key] = true;
                sealModal(s, true, live);
                return;
            }
        }
        // A DIFFERENT seal's modal is open: leave their typing alone — the
        // wait degrades honestly to the hold, and the ✍ chip still stands.
    } else {
        _liveShown = {};
        if (openModal && openModal.dataset.live === '1') expireCountdown(openModal);
    }
    // Never steal focus from someone mid-sentence, and never mark a blank
    // "prompted" unless its popup actually opened — marking first meant a
    // room's SECOND open blank was recorded as shown while the first held
    // the modal, so it never auto-raised at all (finding 3.3).
    const composing = document.activeElement;
    const busy = !!document.getElementById('st-seal-modal')
        || (composing && /^(TEXTAREA|INPUT)$/.test(composing.tagName) && composing.value);
    for (const s of seals) {
        const heldBump = (s.held || 0) > (_sealHeld[s.key] || 0);
        if (s.revealed || s.filled || s.skipped) { _sealHeld[s.key] = s.held || 0; continue; }
        // Single trigger (Krem 2026-08-06): a popup only ever raises because
        // she reached for the blank — the live countdown when you're here,
        // this urgent catch-up when you weren't. Entry never ambushes; the
        // ✍ chip is the player's volitional early-fill.
        if (!(s.held > 0)) { _sealHeld[s.key] = s.held || 0; continue; }
        if (_sealPrompted[s.key] && !heldBump) { _sealHeld[s.key] = s.held || 0; continue; }
        // busy: leave the bump UNconsumed so the next tick still sees it —
        // recording it here meant an urgent popup suppressed once (composer
        // in use) never auto-raised again (post-fix review 2026-08-05).
        if (busy) continue;
        _sealHeld[s.key] = s.held || 0;
        _sealPrompted[s.key] = true;
        sealModal(s, true);
        return;                                  // one popup at a time
    }
}

// ── countdown (live wait) ──
// wrap._count = {remaining, total, timer}; the 1s tick self-clears when the
// modal leaves the DOM, and each 12s/nudge poll resyncs remaining from the
// server so extensions and drift both heal.

function paintCount(wrap) {
    const c = wrap._count;
    if (!c) return;
    const bar = wrap.querySelector('#st-seal-bar');
    const secs = wrap.querySelector('#st-seal-secs');
    if (bar) bar.style.width = Math.max(0, Math.min(100, (c.remaining / c.total) * 100)) + '%';
    if (secs) secs.textContent = c.remaining + 's';
}

function startCountdown(wrap, remaining) {
    wrap.dataset.live = '1';
    const row = wrap.querySelector('#st-seal-count');
    if (row) row.style.display = 'flex';
    wrap._count = { remaining: Math.max(0, remaining), total: Math.max(remaining, 1) };
    paintCount(wrap);
    wrap._count.timer = setInterval(() => {
        if (!document.body.contains(wrap)) { clearInterval(wrap._count?.timer); return; }
        if (!wrap._count) return;
        wrap._count.remaining -= 1;
        if (wrap._count.remaining <= 0) { expireCountdown(wrap); return; }
        paintCount(wrap);
    }, 1000);
}

function syncCountdown(wrap, remaining) {
    if (!wrap._count) { startCountdown(wrap, remaining); return; }   // upgrade in place
    if (remaining <= 0) { expireCountdown(wrap); return; }
    wrap._count.remaining = remaining;
    wrap._count.total = Math.max(wrap._count.total, remaining);
    paintCount(wrap);
}

function expireCountdown(wrap) {
    if (wrap._count?.timer) clearInterval(wrap._count.timer);
    wrap._count = null;
    wrap.dataset.live = '';
    const row = wrap.querySelector('#st-seal-count');
    if (row) row.innerHTML = `<span class="st-card-note" style="margin:0">the moment passed — she moved on; seal it anyway and she finds it on her next try</span>`;
}

function sealModal(seal, urgent, live) {
    const esc = host.esc;
    document.getElementById('st-seal-modal')?.remove();
    const wrap = document.createElement('div');
    wrap.id = 'st-seal-modal';
    wrap.dataset.key = seal.key;
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:10000';
    wrap.innerHTML = `
        <div class="st-card" style="max-width:min(560px,92vw)">
            <div class="st-card-title">&#x270D; The story left this blank for you</div>
            ${urgent ? `<div class="st-card-desc" style="color:var(--warning,#e8b339)">${live ? "She's reaching for it RIGHT NOW — she waits while you write." : "She's reaching for it right now — she holds off until you write it."}</div>` : ''}
            <div class="st-card-desc">${esc(seal.ask || '')}</div>
            <div id="st-seal-count" style="display:none;align-items:center;gap:8px;margin:2px 0 6px">
                <div style="flex:1;height:6px;background:var(--bg-secondary,#161b26);border-radius:3px;overflow:hidden"><div id="st-seal-bar" style="height:100%;width:100%;background:var(--warning,#e8b339);transition:width 1s linear"></div></div>
                <span id="st-seal-secs" style="font-variant-numeric:tabular-nums;color:var(--text-muted,#999);min-width:3.5ch;text-align:right"></span>
                <button type="button" class="btn-sm" id="st-seal-more" title="+60 seconds">&#x23F3; More time</button>
            </div>
            <textarea id="st-seal-text" rows="4" style="width:100%;box-sizing:border-box;margin:8px 0;background:var(--bg-secondary,#161b26);color:var(--text,#e1e1e6);border:1px solid var(--border,#333);border-radius:6px;padding:8px">${esc(seal.text || '')}</textarea>
            <div class="st-card-verbs">
                <button type="button" class="btn-sm" id="st-seal-save">&#x1F512; Seal it in</button>
                ${seal.has_fallback && !seal.filled ? `<button type="button" class="btn-sm" id="st-seal-skip" title="Use the author's line instead">${live ? "Use author's line" : 'Skip'}</button>` : ''}
                <button type="button" class="btn-sm" id="st-seal-later">Later</button>
            </div>
            <div class="st-card-note">what you write never passes through her — she discovers it in play</div>
        </div>`;
    const refresh = async () => {
        try { _status = (await api('story/status')).active; paintPanel(); } catch (e) { /* poll heals */ }
    };
    const post = async (body, failMsg) => {
        try {
            const r = await api('story/fill', 'POST', body);
            if (!r.success) throw new Error(r.detail || failMsg);
            ui.showToast(r.detail, 'success', 3500);
            wrap.remove(); refresh();
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
    wrap.querySelector('#st-seal-save').onclick = () => {
        const text = wrap.querySelector('#st-seal-text').value.trim();
        if (!text) { ui.showToast('Write something — the blank is the whole point.', 'warning'); return; }
        post({ key: seal.key, text }, 'seal failed');
    };
    const skipBtn = wrap.querySelector('#st-seal-skip');
    if (skipBtn) skipBtn.onclick = () => post({ key: seal.key, skip: true }, 'skip failed');
    wrap.querySelector('#st-seal-more').onclick = async () => {
        try {
            const r = await api('story/wait', 'POST', { key: seal.key });
            if (r.success && r.remaining != null) syncCountdown(wrap, r.remaining);
            else expireCountdown(wrap);           // the moment already passed
        } catch (e) { /* poll heals */ }
    };
    wrap.querySelector('#st-seal-later').onclick = () => wrap.remove();
    wrap.addEventListener('click', e => { if (e.target === wrap) wrap.remove(); });
    document.body.appendChild(wrap);
    if (live) startCountdown(wrap, live.remaining);
    wrap.querySelector('#st-seal-text').focus();
}

// ------------------------------------------------------------------ lightbox
// Show-image fx (Krem 2026-08-22): a `show` effect fired server-side — the
// journal folded it, full_state carries the latest as shown_last {url,
// caption, seq}. Raise it exactly once per seq bump. Arrival baselines
// (resume/refresh never replays an old reveal); the seal modal wins the
// screen (their writing > our picture) — the seq stays unconsumed so the
// next tick retries, same law as the seals' busy check.

let _shownSeq = null;   // last seq raised; null = no baseline yet

function checkShown(a) {
    if (!a) return;
    const s = a.shown_last;
    if (!s) { _shownSeq = 0; return; }        // fresh run (or nothing shown)
    if (_shownSeq === null) { _shownSeq = s.seq; return; }   // arrival baseline
    if (s.seq <= _shownSeq) return;
    if (document.getElementById('st-seal-modal')) return;    // retry next tick
    _shownSeq = s.seq;
    lightbox(s);
}

function lightbox(s) {
    const esc = host.esc;
    document.getElementById('st-lightbox')?.remove();
    const wrap = document.createElement('div');
    wrap.id = 'st-lightbox';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.82);display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:10001;cursor:pointer;gap:14px;padding:24px;box-sizing:border-box';
    wrap.innerHTML = `
        <img src="${esc(s.url)}" alt="" style="max-width:min(1100px,94vw);max-height:80vh;border-radius:10px;box-shadow:0 12px 48px rgba(0,0,0,0.6);object-fit:contain">
        ${s.caption ? `<div style="color:#e8e8ee;font-size:1.05em;text-align:center;max-width:min(900px,90vw);text-shadow:0 1px 4px rgba(0,0,0,0.8)">${esc(s.caption)}</div>` : ''}
        <div class="st-card-note" style="color:#aaa">click anywhere to close</div>`;
    wrap.onclick = () => wrap.remove();
    document.body.appendChild(wrap);
}

// ------------------------------------------------------------------ tap-to-draft

const pretty = n => String(n).replace(/[_-]/g, ' ');
const _noArticle = /^(the|a|an|her|his|my|your|their|our|some)\s/i;
const _the = n => (_noArticle.test(n) ? n : 'the ' + n);

function draftVerb(verb, name) {
    const n = pretty(name);
    if (verb === 'look') return `I take a closer look at ${_the(n)}.`;
    if (verb === 'solve') return `My answer for ${_the(n)}: `;
    return `I ${verb} ${_the(n)}.`;
}

function draftToComposer(text) {
    const input = document.getElementById('prompt-input');
    if (!input) return;
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));   // autosize
    input.focus();
    try { input.setSelectionRange(input.value.length, input.value.length); } catch (e) { /* ok */ }
}

function sceneCard(title, desc, actions) {
    const esc = host.esc;
    document.getElementById('st-card')?.remove();
    const wrap = document.createElement('div');
    wrap.id = 'st-card';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.45);display:flex;align-items:center;justify-content:center;z-index:10000';
    wrap.innerHTML = `
        <div class="st-card">
            <div class="st-card-title">${esc(title)}</div>
            ${desc ? `<div class="st-card-desc">${esc(desc)}</div>` : ''}
            ${actions.length ? `<div class="st-card-verbs">${actions.map((v, i) =>
                `<button type="button" class="btn-sm" data-act="${i}">${esc(v.label)}</button>`).join('')}</div>` : ''}
            <div class="st-card-note">tap an action to draft it — you edit and send</div>
        </div>`;
    wrap.addEventListener('click', e => { if (e.target === wrap) wrap.remove(); });
    wrap.querySelectorAll('[data-act]').forEach(b => b.onclick = () => {
        draftToComposer(actions[+b.dataset.act].draft);
        wrap.remove();
    });
    document.body.appendChild(wrap);
}

// Character screen (Krem 2026-08-25, WoW-screen-lite): the People chip
// opens this — portrait (cast `image`, URL-resolved server-side), who
// plays them, worn gear, parts with tap-to-draft actions, sheet fields.
// Wave 3 adds dressing controls HERE — this card is their future home.
// Slot pins over the portrait (Krem's blank check, 2026-08-26): [side, y%]
// in the 720x1200 standard frame — hat at the head, shoes at the feet.
// Unknown slots stack down the left. Percent-of-image, so any portrait
// at that aspect lines up.
const SLOT_PINS = { hat: ['r', 9], outer: ['r', 25], jacket: ['r', 25], body: ['r', 25],
    shirt: ['r', 37], bra: ['l', 31], pants: ['r', 58], underwear: ['l', 53],
    shoes: ['r', 93], socks: ['l', 87], in_hand: ['l', 46], pack: ['l', 40], holster: ['l', 61] };

// Wardrobe layers (2026-08-26): worn items with a cut-out paint over the
// portrait, bottom to top in this slot order (unknown slots go on top).
const LAYER_ORDER = ['underwear', 'bra', 'socks', 'pants', 'shirt', 'shoes', 'body', 'jacket', 'outer', 'hat', 'in_hand'];

function personCard(cid, c, a) {
    const esc = host.esc;
    document.getElementById('st-card')?.remove();
    document.getElementById('st-slot-menu')?.remove();
    const wrap = document.createElement('div');
    wrap.id = 'st-card';
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;z-index:10000';
    const wearing = c.wearing || {};
    const slots = c.slots || [];
    const icons = (a && a.icons) || {};
    const layerUrls = (a && a.layers) || {};
    const layers = Object.entries(wearing)
        .filter(([, it]) => it && layerUrls[it])
        .sort((x, y) => { const o = sl => { const i = LAYER_ORDER.indexOf(sl); return i < 0 ? 99 : i; }; return o(x[0]) - o(y[0]); })
        .map(([, it]) => `<img src="${esc(layerUrls[it])}" alt="" style="position:absolute;left:0;top:0;height:100%;width:100%;pointer-events:none">`)
        .join('');
    // Pins live INSIDE the portrait box (wrapper shrinks to the image, so
    // percent offsets are image coordinates). Translucent over the art.
    // Uniform square slot (Krem 2026-08-26, HUX): tiny label ABOVE, the item
    // icon fills the square, no item name until clicked. Empty = dashed.
    const tile = (sl) => {
        const it = wearing[sl];
        const ic = it && icons[it];
        const inner = ic ? `<img src="${esc(ic)}" alt="" style="width:100%;height:100%;object-fit:cover;display:block">`
            : (it ? `<div style="font-size:.6em;line-height:1.15;padding:3px;text-align:center;overflow:hidden">${esc(pretty(it))}</div>` : '');
        return `<div style="font-size:.58em;letter-spacing:.07em;text-transform:uppercase;color:#fff;text-shadow:0 1px 3px #000,0 0 2px #000;opacity:.9;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(pretty(sl))}</div>
            <div style="width:64px;height:64px;border-radius:9px;border:1px ${it ? 'solid rgba(255,255,255,.5)' : 'dashed rgba(255,255,255,.45)'};background:rgba(10,10,14,.62);backdrop-filter:blur(3px);display:flex;align-items:center;justify-content:center;overflow:hidden;color:#fff">${inner}</div>`;
    };
    let extraY = 66;
    const pins = slots.map(sl => {
        const [side, y] = SLOT_PINS[sl] || ['l', (extraY += 9)];
        const it = wearing[sl];
        return `<div data-slot="${esc(sl)}" data-side="${side}" data-y="${y}" title="${esc(it ? pretty(it) : pretty(sl))}" style="position:absolute;top:${y}%;${side === 'r' ? 'right:8px' : 'left:8px'};transform:translateY(-50%);width:64px;cursor:pointer;text-align:center">${tile(sl)}</div>`;
    }).join('');
    // No portrait: the grid stands in (same popover on click).
    const slotBoxes = slots.map(sl => {
        const it = wearing[sl];
        return `<div data-slot="${esc(sl)}" title="${esc(it ? pretty(it) : pretty(sl))}" style="width:64px;cursor:pointer;text-align:center">${tile(sl)}</div>`;
    }).join('');
    const acts = [];
    const parts = Object.entries(c.parts || {}).map(([pn, p]) => {
        const btns = (p.verbs || []).map(v => {
            acts.push({ draft: draftVerb(v, `${cid}_${pn}`) });
            return `<button type="button" class="btn-sm" data-act="${acts.length - 1}">${esc(v)}</button>`;
        }).join('');
        const pst = Object.entries(p.state || {}).map(([k, v]) => `${pretty(k)}: ${v}`).join(' · ');
        return `<div style="margin:4px 0"><b>✋ ${esc(pretty(pn))}</b>${pst ? ` — ${esc(pst)}` : ''}${p.desc ? `<div style="opacity:.85;font-size:.9em">${esc(p.desc)}</div>` : ''}${btns ? `<div class="st-card-verbs">${btns}</div>` : ''}</div>`;
    }).join('');
    // Party inventory is SHARED (engine truth until per-char NPC pockets).
    const carrying = ((a && a.inventory) || []).map(i =>
        `<span class="st-exit st-obj" style="display:inline-flex;align-items:center;gap:5px">${icons[i] ? `<img src="${esc(icons[i])}" alt="" style="width:22px;height:22px;border-radius:4px;object-fit:cover">` : ''}${esc(pretty(String(i)))}</span>`).join('');
    const fields = Object.entries(c.fields || {}).map(([k, v]) => `${pretty(k)}: ${v}`).join(' · ');
    const sec = (label, body) => body
        ? `<div style="font-weight:600;font-size:1.08em;margin:10px 0 3px">${label}</div><div style="padding:0 0 4px">${body}</div>` : '';
    const titleFace = !c.image && c.badge
        ? `<img src="${esc(c.badge)}" alt="" style="width:34px;height:34px;border-radius:50%;object-fit:cover;vertical-align:-10px;margin-right:6px">`
        : (c.image ? '' : '\u{1F464} ');
    // Portrait column shrinks to the image; the card's height is capped by
    // viewport height AND width (0.6 aspect) so the sidebar keeps room.
    wrap.innerHTML = `
        <div class="st-card" style="width:min(920px,94vw);max-width:min(920px,94vw);${c.image ? 'height:min(86vh,840px,calc(94vw * 0.9));' : 'max-height:86vh;'}position:relative;display:flex;gap:14px;padding:14px;overflow:hidden;text-align:left">
            <button type="button" data-close title="Close" style="position:absolute;top:8px;right:12px;background:none;border:none;color:inherit;font-size:1.15em;cursor:pointer;opacity:.65;z-index:2">✕</button>
            ${c.image ? `<div style="flex:0 0 auto;height:100%;position:relative"><img src="${esc(c.image)}" alt="" style="height:100%;display:block">${layers}${pins}</div>` : ''}
            <div style="flex:1;min-width:0;overflow-y:auto;align-self:stretch">
                <div class="st-card-title" style="margin-bottom:0;font-size:1.45em">${titleFace}${esc(pretty(c.name || cid))}</div>
                <div class="st-card-note" style="text-align:left;margin:0 0 8px">${c.controlled_by === 'player' ? 'your character' : 'played by the storyteller'}</div>
                ${sec('Bio', c.desc ? `<div style="white-space:pre-wrap">${esc(c.desc)}</div>` : '')}
                ${c.image ? '' : sec('Wearing', slotBoxes ? `<div style="display:flex;flex-wrap:wrap;gap:10px">${slotBoxes}</div>` : '')}
                ${sec('Interact', parts)}
                ${sec('Carrying (shared)', carrying)}
                ${fields ? `<div style="margin-top:6px;opacity:.9">${esc(fields)}</div>` : ''}
            </div>
        </div>`;
    // ── lifecycle: one close(), Escape aware, menu-aware backdrop ──
    let menu = null, menuFor = null;
    const closeMenu = () => { if (menu) { menu.remove(); menu = null; menuFor = null; } };
    const onKey = e => { if (e.key === 'Escape') { if (menu) closeMenu(); else close(); } };
    // Pin layout (Krem 2026-08-26, "in-hand and pack overlap"): anchors are
    // preferences; per side, pins sort by anchor and get pushed apart to a
    // real pixel gap (tile height + 6), then the chain slides up if it ran
    // off the bottom. Re-run on resize — the portrait's height moves.
    const layoutPins = () => {
        const box = wrap.querySelector('img')?.parentElement;
        if (!box) return;
        const H = box.clientHeight;
        if (!H) return;
        for (const side of ['l', 'r']) {
            const els = [...wrap.querySelectorAll(`[data-side="${side}"]`)]
                .sort((p, q) => +p.dataset.y - +q.dataset.y);
            if (!els.length) continue;
            const h = els[0].offsetHeight || 84, gap = h + 6;
            const ys = els.map(el => Math.max(h / 2, Math.min(H - h / 2, H * (+el.dataset.y) / 100)));
            for (let i = 1; i < ys.length; i++) ys[i] = Math.max(ys[i], ys[i - 1] + gap);
            const over = ys[ys.length - 1] - (H - h / 2);
            if (over > 0) { for (let i = 0; i < ys.length; i++) ys[i] -= over; }
            for (let i = ys.length - 2; i >= 0; i--) ys[i] = Math.min(ys[i], ys[i + 1] - gap);
            els.forEach((el, i) => { el.style.top = `${Math.max(h / 2, ys[i])}px`; });
        }
    };
    const onResize = () => layoutPins();
    window.addEventListener('resize', onResize);
    const close = () => { closeMenu(); document.removeEventListener('keydown', onKey); window.removeEventListener('resize', onResize); wrap.remove(); };
    document.addEventListener('keydown', onKey);
    wrap.addEventListener('click', e => { if (menu) { closeMenu(); return; } if (e.target === wrap) close(); });
    wrap.querySelector('[data-close]').onclick = close;
    const redress = async (action, item) => {
        try {
            const r = await api('story/wear', 'POST', { char: cid, action, item });
            if (!r.success) { ui.showToast(r.detail || 'refused', 'error'); return; }
            if (r.detail) ui.showToast(r.detail, 'success');
            _status = (await api('story/status')).active;
            paintPanel();
            const c2 = _status && _status.cast && _status.cast[cid];
            close();
            if (c2) personCard(cid, c2, _status);
        } catch (e) { ui.showToast(e.message, 'error'); }
    };
    wrap.querySelectorAll('[data-act]').forEach(b => b.onclick = () => {
        draftToComposer(acts[+b.dataset.act].draft);
        close();
    });
    // Slot popover: floats over everything, reflows nothing (the old inline
    // <select> grew its box — Krem's jank report). Flips up near the bottom.
    const openMenu = (pin, sl) => {
        closeMenu();
        menuFor = pin;
        const cur = wearing[sl] || '';
        const cands = Object.entries((a && a.wearables) || {}).filter(([, ws]) => ws === sl).map(([n]) => n);
        const r = pin.getBoundingClientRect();
        menu = document.createElement('div');
        menu.id = 'st-slot-menu';
        menu.style.cssText = 'position:fixed;z-index:10001;min-width:180px;max-width:280px;background:var(--bg-secondary,#1e1e26);color:var(--text-primary,#eee);border:1px solid var(--border-color,#555);border-radius:8px;padding:4px;box-shadow:0 8px 24px rgba(0,0,0,.5)';
        const row = (label, val, on) => `<button type="button" data-pick="${esc(val)}" style="display:flex;align-items:center;gap:8px;width:100%;text-align:left;background:${on ? 'rgba(255,255,255,.12)' : 'none'};border:none;color:inherit;padding:5px 8px;border-radius:6px;cursor:pointer">${val && icons[val] ? `<img src="${esc(icons[val])}" alt="" style="width:28px;height:28px;border-radius:5px;object-fit:cover;flex:0 0 auto">` : `<span style="width:28px;flex:0 0 auto"></span>`}<span>${label}</span></button>`;
        menu.innerHTML = `<div style="font-size:.68em;opacity:.7;text-transform:uppercase;letter-spacing:.06em;padding:4px 8px 2px">${esc(pretty(sl))}</div>`
            + (cur ? row(`${esc(pretty(cur))} <span style="opacity:.6">(worn)</span>`, cur, true) : '')
            + cands.map(n => row(esc(pretty(n)), n, false)).join('')
            + (!cur && !cands.length ? `<div style="padding:4px 8px 6px;opacity:.55;font-size:.82em">nothing in the inventory fits here</div>` : '')
            + row(cur ? '— remove —' : '— none —', '', !cur);
        document.body.appendChild(menu);
        const mw = menu.offsetWidth, mh = menu.offsetHeight;
        let top = r.bottom + 4;
        if (top + mh > window.innerHeight - 8) top = Math.max(8, r.top - mh - 4);
        const left = Math.min(Math.max(8, r.left), window.innerWidth - mw - 8);
        menu.style.top = `${top}px`; menu.style.left = `${left}px`;
        menu.querySelectorAll('[data-pick]').forEach(b => b.onclick = e => {
            e.stopPropagation();
            const v = b.dataset.pick;
            closeMenu();
            if (!v && cur) redress('remove', sl);
            else if (v && v !== cur) redress('wear', v);
        });
    };
    wrap.querySelectorAll('[data-slot]').forEach(pin => pin.onclick = e => {
        e.stopPropagation();
        if (menu && menuFor === pin) { closeMenu(); return; }   // same square = toggle
        openMenu(pin, pin.dataset.slot);
    });
    document.body.appendChild(wrap);
    layoutPins();
    const img = wrap.querySelector('img');
    if (img && !img.complete) img.onload = layoutPins;
}

