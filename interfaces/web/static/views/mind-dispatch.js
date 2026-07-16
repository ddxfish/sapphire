// views/mind-dispatch.js - Mind view dispatcher. Three Mind flyout ids
// (memories / people / knowledge) point here; the ?view= query param on the
// import URL tells each instance which view it is (distinct URLs = distinct
// module instances, so state below is per-view).
//
// On every show() it probes the mindpalace plugin's status route and delegates
// to the palace or classic implementation. Live probe, not boot-time: a plugin
// toggle mid-session swaps the Mind views on next visit without a reload
// (stale-UI-cache class, 2026-06-24). Classic modules are untouched — this is
// the whole point: zero regression risk to the fallback system.

const params = new URL(import.meta.url).searchParams;
const VIEW = params.get('view') || 'memories';
const _v = params.get('v') ? `?v=${params.get('v')}` : '';

const CLASSIC = {
    memories: `./memories.js${_v}`,
    people: `./people.js${_v}`,
    knowledge: `./knowledge.js${_v}`,
    goals: `./goals.js${_v}`,
    // 'self' and 'admin' have no classic equivalent — palace-only (show()).
};
const PALACE = {
    self: `./palace/self.js${_v}`,
    memories: `./palace/memories.js${_v}`,
    people: `./palace/entities.js${_v}`,
    knowledge: `./palace/knowledge.js${_v}`,
    goals: `./palace/goals.js${_v}`,
    admin: `./palace/admin.js${_v}`,
};

// Palace-only views: friendly pointer instead of a missing classic module.
const PALACE_ONLY = {
    self: ['\u{1F4A0} Self', 'The self layer lives in the Mind Palace memory system. Enable the Mind Palace plugin to give Sapphire a self-sheet.'],
    admin: ['\u{1F6E0}️ Admin', 'The Admin console belongs to the Mind Palace memory system. Enable the Mind Palace plugin to manage the librarian, migration, and rescue tools.'],
};

let container = null;
let impl = null;        // the active implementation module's default export
let implKind = null;    // 'palace' | 'classic'

// Probe cache shared across all three dispatcher instances via window —
// module scope is per-instance here. Short TTL: one probe per Mind visit,
// not one per tab click within it.
async function palaceActive() {
    const cached = window._palaceProbe;
    if (cached && Date.now() - cached.ts < 3000) return cached.active;
    let active = false;
    try {
        const r = await fetch('/api/plugin/mindpalace/status', { credentials: 'same-origin' });
        active = r.ok;
    } catch { active = false; }
    window._palaceProbe = { ts: Date.now(), active };
    return active;
}

export default {
    init(el) { container = el; },
    async show() {
        const active = await palaceActive();
        const wantKind = active ? 'palace' : 'classic';
        if (PALACE_ONLY[VIEW] && !active) {
            if (impl?.hide) { try { impl.hide(); } catch {} }
            impl = null; implKind = null;
            const [title, note] = PALACE_ONLY[VIEW];
            container.innerHTML = `<div class="view-placeholder">
                <h2>${title}</h2>
                <p style="color:var(--text-muted);font-size:var(--font-sm)">${note}</p>
            </div>`;
            return;
        }
        if (implKind !== wantKind) {
            if (impl?.hide) { try { impl.hide(); } catch {} }
            try {
                const mod = await import((wantKind === 'palace' ? PALACE : CLASSIC)[VIEW]);
                impl = mod.default;
                implKind = wantKind;
                impl.init(container);
            } catch (e) {
                console.error(`[MindDispatch] Failed to load ${wantKind}/${VIEW}:`, e);
                container.innerHTML = `<div class="view-placeholder">
                    <h2>Failed to load ${VIEW}</h2>
                    <p style="color:var(--text-muted);font-size:var(--font-sm)">${e.message}</p>
                    <p style="color:var(--text-muted);font-size:var(--font-sm)">Try a hard refresh (Ctrl+Shift+R)</p>
                </div>`;
                impl = null; implKind = null;
                return;
            }
        }
        await impl.show();
    },
    hide() { if (impl?.hide) impl.hide(); }
};
