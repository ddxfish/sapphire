// surface/stage.js — the Chat page's stage: one app beside the conversation
// (tmp/stage-plan.md, 2026-10-25).
//
// A plugin app that lists "stage" in its manifest `app.places` can be opened
// here, left of the chat rail, from the 🖥 picker in the sidebar header or a
// STAGE card in her reply (open_stage). The tenant is the same module the
// Apps page mounts — `render(el, ctx)` returning a cleanup — plus a ctx:
//   ctx.chat          { name, private } of the chat beside it, kept current
//   ctx.onChange(fn)  called with the new ctx.chat on every switch
//
// Rules this module holds so no tenant has to (privacy scout, 2026-10-25):
// - A private chat never has a stage: the tenant is torn down when the chat
//   beside it is or becomes private (switch, talk-stamp, vault, remote
//   switch); the picker greys out. Unknown privacy counts as private.
// - The stage never moves the active-chat pointer and never borrows the
//   rail (surface/organs.js); tenants address their chat by name.
// - What is open is this TAB's (sessionStorage), never a chat setting and
//   never on the event bus, so it doesn't follow you to the phone.
// - The tenant runs only while the Chat view is on screen.
import * as eventBus from '../core/event-bus.js';
import * as api from '../api.js';
import { showToast } from '../ui.js';

const OPEN_KEY = 'sapphire-stage-open';        // sessionStorage: this tab's open app
const WIDTH_KEY = 'sapphire-stage-width';      // localStorage: the column's width

let els = null;
let wanted = null;          // {name, label, icon} — what this tab asked for
let live = null;            // {name, cleanup, listeners} — what is mounted now
let visible = false;        // the Chat view is on screen
let chat = { name: '', private: true };
let epoch = 0;              // supersede token for mounts and privacy reads
let checkSeq = 0;

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export async function stageApps() {
    try {
        const r = await fetch('/api/apps', { signal: AbortSignal.timeout(10000) });
        const d = r.ok ? await r.json() : { apps: [] };
        return (d.apps || []).filter(a => (a.places || []).includes('stage'));
    } catch {
        return [];
    }
}

export function initStage(container) {
    const stage = container.querySelector('.chat-stage');
    if (!stage || els) return;
    els = {
        body: container.querySelector('.chat-body'),
        stage,
        gutter: container.querySelector('.stage-resizer'),
        mount: stage.querySelector('.chat-stage-mount'),
        title: stage.querySelector('.chat-stage-title'),
        btn: container.querySelector('#sb-stage-btn'),
        menu: container.querySelector('#sb-stage-menu'),
    };
    const w = parseInt(localStorage.getItem(WIDTH_KEY) || '', 10);
    if (w) stage.style.flexBasis = `${w}px`;
    stage.querySelector('.chat-stage-close').addEventListener('click', closeStage);
    els.btn?.addEventListener('click', (e) => { e.stopPropagation(); toggleMenu(); });
    document.addEventListener('click', (e) => {
        if (els.menu && !els.menu.hidden && !e.target.closest('#sb-stage-pick')) els.menu.hidden = true;
    });
    initGutter();
    try { wanted = JSON.parse(sessionStorage.getItem(OPEN_KEY) || 'null'); } catch { wanted = null; }

    // Every way the chat beside the stage can change, or change privacy
    const sel = document.getElementById('chat-select');
    sel?.addEventListener('chat-activated', (e) => recheck(e.detail || null));
    sel?.addEventListener('chat-list-ready', () => recheck());
    eventBus.on(eventBus.Events.CHAT_SWITCHED, () => recheck());
    eventBus.on(eventBus.Events.CHAT_SETTINGS_CHANGED, (d) => { if (!d?.chat || d.chat === chat.name) recheck(); });
    eventBus.on(eventBus.Events.PROMPT_CHANGED, (d) => { if (d?.action === 'vault_changed') recheck(); });
}

// The Chat view came on screen or left it (views/chat.js show/hide).
export function setStageVisible(on) {
    visible = !!on;
    if (visible) recheck(); else apply();
}

export async function openStage(name) {
    const app = (await stageApps()).find(a => a.name === name);
    if (!app) {
        showToast(`No stage app named "${name}" is enabled.`, 'warning');
        return false;
    }
    wanted = { name: app.name, label: app.label, icon: app.icon };
    try { sessionStorage.setItem(OPEN_KEY, JSON.stringify(wanted)); } catch {}
    await recheck();
    if (chat.private) showToast('The stage stays closed in private chats.', 'info');
    return !chat.private;
}

export function closeStage() {
    wanted = null;
    try { sessionStorage.removeItem(OPEN_KEY); } catch {}
    apply();
}

// Read the chat beside the stage and its privacy, then mount or tear down.
// detail = chat-activated's {chat, settings} when that's what fired.
async function recheck(detail = null) {
    const my = ++checkSeq;
    const name = document.getElementById('chat-select')?.value || '';
    let settings = null;
    if (detail && detail.chat === name && detail.settings && typeof detail.settings === 'object') {
        settings = detail.settings;
    } else if (name) {
        try { settings = (await api.getChatSettings(name))?.settings || null; } catch { settings = null; }
    }
    if (my !== checkSeq) return;                                 // a newer read is on its way
    const next = { name, private: !settings || !!settings.private_chat };   // unknown = private
    const changed = next.name !== chat.name || next.private !== chat.private;
    chat = next;
    apply();
    if (changed && live) for (const fn of live.listeners) { try { fn({ ...chat }); } catch (e) { console.warn('[Stage] onChange', e); } }
}

function apply() {
    if (!els) return;
    const show = visible && !!wanted && !!chat.name && !chat.private;
    if (show && (!live || live.name !== wanted.name)) mount(wanted);
    else if (!show && live) unmount();
    if (!show && !live) hideFrame();
    if (els.btn) {
        els.btn.classList.toggle('active', !!live);
        els.btn.disabled = chat.private;
        els.btn.title = chat.private ? 'Stage — closed in private chats'
                                     : 'Stage — open an app beside the chat';
    }
    if (chat.private && els.menu) els.menu.hidden = true;
}

async function mount(app) {
    unmount();
    const mine = ++epoch;
    live = { name: app.name, cleanup: null, listeners: new Set() };
    const me = live;
    els.title.textContent = `${app.icon || ''} ${app.label || app.name}`.trim();
    els.stage.hidden = false;
    els.gutter.hidden = false;
    els.body.classList.add('has-stage');
    const ctx = {
        chat: { ...chat },
        onChange: (fn) => { me.listeners.add(fn); return () => me.listeners.delete(fn); },
    };
    const v = document.querySelector('meta[name="boot-version"]')?.content || '';
    let mod = null;
    try {
        mod = await import(`/plugin-web/${app.name}/app/index.js?v=${v}`);
        if (mine !== epoch) return;
        const ret = mod.render ? await mod.render(els.mount, ctx) : null;
        const fin = typeof ret === 'function' ? ret : (mod.cleanup || null);
        if (mine !== epoch) { try { fin?.(); } catch {} return; }   // superseded mid-render
        me.cleanup = fin;
    } catch (e) {
        if (mine !== epoch) return;
        try { mod?.cleanup?.(); } catch {}
        console.error(`[Stage] '${app.name}' failed to open:`, e);
        els.mount.innerHTML = `<div class="view-placeholder">${esc(app.label || app.name)} could not open: ${esc(e.message || e)}</div>`;
    }
}

function unmount() {
    epoch++;
    const was = live;
    live = null;
    if (was?.cleanup) { try { was.cleanup(); } catch (e) { console.warn('[Stage] cleanup', e); } }
    if (els) els.mount.replaceChildren();
    hideFrame();
}

function hideFrame() {
    if (!els) return;
    els.stage.hidden = true;
    els.gutter.hidden = true;
    els.body.classList.remove('has-stage');
}

async function toggleMenu() {
    const menu = els.menu;
    if (!menu) return;
    if (!menu.hidden) { menu.hidden = true; return; }
    if (chat.private) return;
    menu.innerHTML = '<div class="sb-stage-empty">Loading…</div>';
    menu.hidden = false;
    const apps = await stageApps();
    const rows = apps.map(a => `<button type="button" data-app="${esc(a.name)}" class="${wanted?.name === a.name ? 'on' : ''}">`
        + `<span>${esc(a.icon || '📦')}</span> ${esc(a.label)}</button>`);
    if (wanted) rows.push('<button type="button" data-close="1">Close the stage</button>');
    menu.innerHTML = rows.join('') || '<div class="sb-stage-empty">No stage apps enabled.</div>';
    menu.querySelectorAll('[data-app]').forEach(b => b.addEventListener('click', () => { menu.hidden = true; openStage(b.dataset.app); }));
    menu.querySelector('[data-close]')?.addEventListener('click', () => { menu.hidden = true; closeStage(); });
}

function initGutter() {
    const g = els.gutter;
    g.onpointerdown = (e) => {
        e.preventDefault();
        g.setPointerCapture(e.pointerId);
        const body = els.body.getBoundingClientRect();
        g.onpointermove = (ev) => {
            const w = Math.max(280, Math.min(ev.clientX - body.left, body.width - 320));
            els.stage.style.flexBasis = `${Math.round(w)}px`;
        };
        g.onpointerup = g.onpointercancel = (ev) => {
            g.onpointermove = g.onpointerup = g.onpointercancel = null;
            try { g.releasePointerCapture(ev.pointerId); } catch {}
            const w = parseInt(els.stage.style.flexBasis, 10);
            if (w) localStorage.setItem(WIDTH_KEY, String(w));
        };
    };
    g.ondblclick = () => { els.stage.style.flexBasis = ''; localStorage.removeItem(WIDTH_KEY); };
}
