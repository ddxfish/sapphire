// plugins/agents/app/index.js — Agents: the page behind the pills (Apps > Agents).
//
// One chat at a time: its live agents (status, progress, pending question with
// lettered buttons, say, stop, transcript), its resting/finished rows, what
// waits in its inbox, and a form to spawn a new agent of any loaded kind. Every
// call is chat-local (core/routes/agents.py); live updates ride the SSE bus.
// This is Trinity's pane viewer done structured — no scraped terminal.
import * as eventBus from '/static/core/event-bus.js';

const API = '/api/agents';
const POLL_MS = 4000;
const LIVE = new Set(['pending', 'running', 'waiting', 'idle']);
const COLORS = { running: '#f0ad4e', pending: '#f0ad4e', waiting: '#b07cff', idle: '#5bc0de', resting: '#7a8fa6',
                 done: '#5cb85c', failed: '#d9534f', stopped: '#888', lost: '#a66' };

let root = null, state = null, timer = null, unsubs = [];

const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
const headers = () => ({ 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() });
const get = async (url) => { const r = await fetch(url, { headers: headers() }); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`); return r.json(); };
const post = async (url, body) => { const r = await fetch(url, { method: 'POST', headers: headers(), body: JSON.stringify(body) }); const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || j.error || `HTTP ${r.status}`); return j; };
const ago = (t) => { if (!t) return ''; const s = Math.max(0, Math.round(Date.now() / 1000 - t)); return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`; };

function injectStyles() {
    if (document.getElementById('agents-app-css')) return;
    const st = document.createElement('style');
    st.id = 'agents-app-css';
    st.textContent = `
      .ag-app { display: grid; grid-template-columns: 220px 1fr; gap: 16px; height: 100%; min-height: 0; color: var(--text); }
      .ag-side { border-right: 1px solid var(--border); padding-right: 12px; overflow: auto; }
      .ag-side h3, .ag-main h3 { font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--text-muted); margin: 8px 0; }
      .ag-chat { display: flex; justify-content: space-between; padding: 6px 8px; border-radius: 6px; cursor: pointer; }
      .ag-chat:hover { background: var(--bg-tertiary); } .ag-chat.sel { background: var(--bg-tertiary); font-weight: 600; }
      .ag-chat .n { color: var(--text-muted); font-size: 12px; }
      .ag-main { overflow: auto; padding-right: 8px; }
      .ag-card { border: 1px solid var(--border); border-left: 4px solid #888; border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; background: var(--bg-secondary, transparent); }
      .ag-card .head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
      .ag-card .name { font-weight: 600; } .ag-card .kind, .ag-card .prog { color: var(--text-muted); font-size: 12px; }
      .ag-card .mission { margin: 6px 0; font-size: 13px; white-space: pre-wrap; }
      .ag-q { border: 1px dashed #b07cff; border-radius: 6px; padding: 8px; margin: 8px 0; }
      .ag-q .qt { font-weight: 600; margin-bottom: 6px; }
      .ag-q button, .ag-row button, .ag-new button { padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg-tertiary); color: var(--text); cursor: pointer; font-size: 13px; }
      .ag-q button:hover, .ag-row button:hover, .ag-new button:hover { border-color: var(--trim); }
      .ag-q .opts { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
      .ag-row { display: flex; gap: 6px; margin-top: 8px; align-items: center; }
      .ag-row input, .ag-new input, .ag-new textarea, .ag-new select { flex: 1; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg-tertiary); color: var(--text); font-size: 13px; }
      .ag-tr { margin-top: 8px; font-family: ui-monospace, monospace; font-size: 12px; color: var(--text-muted); max-height: 220px; overflow: auto; white-space: pre-wrap; background: var(--bg-tertiary); border-radius: 6px; padding: 6px 8px; }
      .ag-tr .ev-text { color: var(--text); } .ag-tr .ev-ask, .ag-tr .ev-answer { color: #b07cff; } .ag-tr .ev-note { opacity: .8; }
      .ag-report { margin-top: 8px; font-size: 13px; white-space: pre-wrap; border-top: 1px solid var(--border); padding-top: 6px; }
      .ag-new { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-top: 16px; display: grid; gap: 8px; }
      .ag-new .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
      .ag-new label { font-size: 12px; color: var(--text-muted); display: grid; gap: 3px; }
      .ag-inbox { font-size: 12px; color: var(--text-muted); margin: 6px 0 12px; }
      .ag-empty { color: var(--text-muted); font-style: italic; margin: 12px 0; }
      .ag-toast { font-size: 12px; margin-top: 4px; min-height: 14px; }
      details.ag-rows summary { cursor: pointer; color: var(--text-muted); font-size: 13px; margin: 10px 0; }
      @media (max-width: 760px) { .ag-app { grid-template-columns: 1fr; } .ag-side { border-right: 0; border-bottom: 1px solid var(--border); padding: 0 0 8px; max-height: 140px; } }
    `;
    document.head.appendChild(st);
}

export async function render(container) {
    injectStyles();
    root = container;
    state = { chats: [], allChats: [], kinds: [], selected: null, data: null, toast: '' };
    container.innerHTML = `
      <div class="ag-app">
        <aside class="ag-side">
          <h3>Chats with agents</h3>
          <div class="ag-chatlist"></div>
          <h3 style="margin-top:14px">Spawn into</h3>
          <select class="ag-pick-chat"></select>
        </aside>
        <main class="ag-main">
          <h3 class="ag-title">Agents</h3>
          <div class="ag-inbox"></div>
          <div class="ag-cards"></div>
          <div class="ag-rowsbox"></div>
          <div class="ag-newbox"></div>
          <div class="ag-toast"></div>
        </main>
      </div>`;
    container.querySelector('.ag-pick-chat').addEventListener('change', (e) => select(e.target.value));
    await Promise.all([loadChats(), loadKinds()]);
    if (!state.selected) {
        const active = document.getElementById('chat-select')?.value;
        select(state.chats[0]?.chat || active || state.allChats[0] || null);
    }
    const bump = () => { loadChats(); if (state.selected) load(state.selected); };
    for (const ev of ['agent_spawned', 'agent_completed', 'agent_dismissed', 'agent_waiting', 'inbox_changed']) {
        unsubs.push(eventBus.on(ev, bump));
    }
    unsubs.push(eventBus.on('agent_event', () => { if (state.selected) load(state.selected); }));
    timer = setInterval(() => {
        if (state.data?.agents?.some(a => LIVE.has(a.status))) load(state.selected);
    }, POLL_MS);
}

export function cleanup() {
    if (timer) clearInterval(timer);
    timer = null;
    for (const u of unsubs) { try { typeof u === 'function' && u(); } catch {} }
    unsubs = [];
    root = null;
}

async function loadChats() {
    try {
        const [withAgents, all] = await Promise.all([get(`${API}/chats`), get('/api/chats')]);
        state.chats = withAgents.chats || [];
        const list = Array.isArray(all) ? all : (all.chats || []);
        state.allChats = list.map(c => (typeof c === 'string' ? c : c.name)).filter(Boolean);
    } catch (e) { toast(e.message); }
    renderSide();
}

async function loadKinds() {
    try { state.kinds = (await get(`${API}/kinds`)).kinds || []; } catch (e) { toast(e.message); }
    renderNew();
}

function select(chat) {
    if (!chat) return;
    state.selected = chat;
    renderSide();
    load(chat);
}

async function load(chat) {
    if (!root || !chat) return;
    try {
        state.data = await get(`${API}/list?chat=${encodeURIComponent(chat)}`);
    } catch (e) { state.data = { chat, agents: [], rows: [], inbox: [] }; toast(e.message); }
    renderMain();
}

function toast(msg) { state.toast = msg || ''; const el = root?.querySelector('.ag-toast'); if (el) el.textContent = state.toast; if (msg) setTimeout(() => { if (state.toast === msg) toast(''); }, 5000); }

function renderSide() {
    if (!root) return;
    const list = root.querySelector('.ag-chatlist');
    list.innerHTML = state.chats.length ? state.chats.map(c =>
        `<div class="ag-chat ${c.chat === state.selected ? 'sel' : ''}" data-chat="${esc(c.chat)}">
           <span>${esc(c.chat)}</span><span class="n">${c.live ? c.live + ' live' : c.rows + ' kept'}</span></div>`).join('')
        : '<div class="ag-empty">none yet</div>';
    list.querySelectorAll('.ag-chat').forEach(el => el.addEventListener('click', () => select(el.dataset.chat)));
    const pick = root.querySelector('.ag-pick-chat');
    pick.innerHTML = state.allChats.map(n => `<option value="${esc(n)}" ${n === state.selected ? 'selected' : ''}>${esc(n)}</option>`).join('');
}

function renderMain() {
    if (!root || !state.data) return;
    const d = state.data;
    root.querySelector('.ag-title').textContent = `Agents in “${d.chat}”`;
    const inbox = d.inbox || [];
    root.querySelector('.ag-inbox').textContent = inbox.length
        ? `Inbox: ${inbox.length} waiting — ${inbox.map(i => `${i.source} (${i.lane}, ${Math.round(i.age)}s)`).join(' · ')}`
        : '';
    const cards = root.querySelector('.ag-cards');
    cards.innerHTML = d.agents.length ? d.agents.map(cardHtml).join('') : '<div class="ag-empty">No agents running in this chat.</div>';
    for (const a of d.agents) wireCard(cards.querySelector(`[data-id="${a.id}"]`), a);
    const rows = root.querySelector('.ag-rowsbox');
    rows.innerHTML = d.rows?.length ? `
      <details class="ag-rows" ${d.agents.length ? '' : 'open'}><summary>${d.rows.length} earlier</summary>
        ${d.rows.map(r => `
          <div class="ag-card" style="border-left-color:${COLORS[r.status] || '#888'}" data-row="${r.id}">
            <div class="head"><span class="name">${esc(r.name)}</span><span class="kind">${esc(r.kind)} · ${esc(r.status)} · ${ago(r.ended || r.started)}</span></div>
            ${r.mission ? `<div class="mission">${esc(r.mission)}</div>` : ''}
            ${r.report_head ? `<div class="ag-report">${esc(r.report_head)}</div>` : ''}
            ${r.status === 'resting' && r.resumable ? `<div class="ag-row"><input placeholder="Say something to wake it…" data-say-row="${r.id}"><button data-say-row-btn="${r.id}">Say</button><button data-stop-row="${r.id}">Forget</button></div>` : ''}
          </div>`).join('')}
      </details>` : '';
    rows.querySelectorAll('[data-say-row-btn]').forEach(b => b.addEventListener('click', () => action(b.dataset.sayRowBtn, 'say', rows.querySelector(`[data-say-row="${b.dataset.sayRowBtn}"]`).value)));
    rows.querySelectorAll('[data-stop-row]').forEach(b => b.addEventListener('click', () => action(b.dataset.stopRow, 'stop', '')));
    renderNew();
}

function cardHtml(a) {
    const q = a.pending_question;
    let qHtml = '';
    if (q) {
        const qs = q.questions || [{ question: q.text, options: [] }];
        qHtml = `<div class="ag-q">${qs.map((x, qi) => `
            <div class="qt">${esc(x.header ? x.header + ': ' : '')}${esc(x.question)}</div>
            <div class="opts">${(x.options || []).map((o, i) => `<button data-answer="${esc(o.label)}" title="${esc(o.description || '')}">(${String.fromCharCode(97 + i)}) ${esc(o.label)}</button>`).join('')}</div>`).join('')}
          <div class="ag-row"><input placeholder="…or answer in your own words" data-answer-text><button data-answer-btn>Answer</button></div>
        </div>`;
    }
    const ev = (a.events || []).map(e => {
        const k = e.kind, dd = e.data;
        let line;
        if (k === 'tool_use' && dd && typeof dd === 'object') line = `${dd.name || '?'} ${dd.input || ''}`;
        else if (k === 'tool_result') line = `  -> ${dd?.is_error ? 'error' : 'ok'}`;
        else if ((k === 'ask' || k === 'answer') && dd && typeof dd === 'object') line = `${k}: ${dd.text || (dd.questions || []).map(x => x.question).join(' | ')}`;
        else line = `${k}: ${typeof dd === 'string' ? dd : JSON.stringify(dd)}`;
        return `<div class="ev-${esc(k)}">${esc(line)}</div>`;
    }).join('');
    return `<div class="ag-card" data-id="${a.id}" style="border-left-color:${COLORS[a.status] || '#888'}">
        <div class="head"><span class="name">${esc(a.name)}</span><span class="kind">${esc(a.kind)}</span><span class="prog">${esc(a.progress || a.status)}</span></div>
        <div class="mission">${esc(a.mission)}</div>
        ${qHtml}
        <div class="ag-row">
          ${a.status === 'idle' ? `<input placeholder="Say a follow-up…" data-say><button data-say-btn>Say</button>` : ''}
          <button data-stop>Stop</button>
          <button data-toggle-tr>Transcript</button>
        </div>
        <div class="ag-tr" hidden>${ev || '<div>nothing yet</div>'}</div>
        ${a.report_head ? `<div class="ag-report">${esc(a.report_head)}</div>` : ''}
      </div>`;
}

function wireCard(el, a) {
    if (!el) return;
    el.querySelectorAll('[data-answer]').forEach(b => b.addEventListener('click', () => action(a.name, 'answer', b.dataset.answer)));
    el.querySelector('[data-answer-btn]')?.addEventListener('click', () => action(a.name, 'answer', el.querySelector('[data-answer-text]').value));
    el.querySelector('[data-say-btn]')?.addEventListener('click', () => action(a.name, 'say', el.querySelector('[data-say]').value));
    el.querySelector('[data-stop]')?.addEventListener('click', () => action(a.name, 'stop', ''));
    el.querySelector('[data-toggle-tr]')?.addEventListener('click', () => { const t = el.querySelector('.ag-tr'); t.hidden = !t.hidden; });
}

async function action(agent, act, value) {
    if (!state.selected) return;
    if ((act === 'answer' || act === 'say') && !String(value || '').trim()) { toast(`${act} needs a value`); return; }
    try {
        const r = await post(`${API}/${encodeURIComponent(agent)}/${act}`, { chat: state.selected, value: value || '' });
        toast(r.message || 'ok');
    } catch (e) { toast(e.message); }
    load(state.selected);
}

function renderNew() {
    if (!root) return;
    const box = root.querySelector('.ag-newbox');
    const kinds = state.kinds.filter(k => k.available);
    if (!kinds.length) { box.innerHTML = '<div class="ag-empty">No agent kinds are loaded — enable the Agents or Claude Code plugin.</div>'; return; }
    const cur = box.querySelector('select[name=kind]')?.value;
    const kind = kinds.find(k => k.kind === cur) || kinds[0];
    box.innerHTML = `<div class="ag-new">
      <h3>New agent in “${esc(state.selected || '')}”</h3>
      <div class="grid">
        <label>Kind<select name="kind">${kinds.map(k => `<option value="${esc(k.kind)}" ${k.kind === kind.kind ? 'selected' : ''}>${esc(k.label)}${k.cloud ? ' (cloud)' : ''}</option>`).join('')}</select></label>
        <label>Name (a workspace/session directory, not the agent's name)<input name="name" placeholder="optional"></label>
      </div>
      <label>Mission<textarea name="mission" rows="3" placeholder="${esc(kind.description || 'what to do, not how')}"></textarea></label>
      <div class="grid">
        ${(kind.spawn_schema || []).map(f => f.options
          ? `<label>${esc(f.label || f.key)}<select name="opt_${esc(f.key)}">${f.options.map(o => { const v = typeof o === 'object' ? o.value : o, l = typeof o === 'object' ? o.label : o; return `<option value="${esc(v)}" ${v === f.default ? 'selected' : ''}>${esc(l)}</option>`; }).join('')}</select></label>`
          : `<label>${esc(f.label || f.key)}<input name="opt_${esc(f.key)}" placeholder="${esc(f.help || '')}" value="${esc(f.default ?? '')}"></label>`).join('')}
        <label>Model<input name="model" placeholder="optional"></label>
      </div>
      <div class="ag-row"><button data-spawn>Spawn</button></div>
    </div>`;
    box.querySelector('select[name=kind]').addEventListener('change', renderNew);
    box.querySelector('[data-spawn]').addEventListener('click', async () => {
        const f = (n) => box.querySelector(`[name="${n}"]`)?.value?.trim() || '';
        const options = {};
        for (const el of box.querySelectorAll('[name^="opt_"]')) if (el.value?.trim()) options[el.name.slice(4)] = el.value.trim();
        if (f('name')) options.name = f('name');
        if (f('model')) options.model = f('model');
        if (!f('mission')) { toast('a mission is needed'); return; }
        try {
            const r = await post(`${API}/spawn`, { chat: state.selected, kind: f('kind'), mission: f('mission'), options });
            toast(r.message || 'spawned');
            box.querySelector('[name=mission]').value = '';
        } catch (e) { toast(e.message); }
        loadChats(); load(state.selected);
    });
}
