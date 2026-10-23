// surface/surface.js — the conversational Surface, Phase 1.
// (Plan: tmp/chat-surface-plan.md. Chat/Game/Story are modes of ONE surface;
// this file owns the shared frame, modes supply the organs.)
//
// Phase 1 scope: a pure frame composer. renderSurface() renders the skeleton
// that used to live statically in templates/index.html into a view container.
// Chat is the sole mode; behavior stays in views/chat.js. Later phases teach
// the engine sessions/dropdown semantics and let plugin modes (game, story)
// import this from their nav-promoted apps — which is why it lives in its own
// directory, importable like /static/audio.js.
//
// A mode supplies markup chunks (trusted, house-authored):
//   { id, cssClass?, mainPane, formArea, sidebarHeader, sidebarBody,
//     accordionNs }
// The frame guarantees the structural classes CSS already targets:
// .chat-body > .chat-main (+ .form-wrapper) + aside.chat-sidebar + expand tab.

// Resizable rail (Krem 2026-08-21): the gutter before the sidebar is a drag
// handle. Width lives in --sb-w on <html> so every Surface page (chat,
// library, game, story) shares one setting; persisted per browser. Mobile
// (<=768px) keeps its fixed overlay width — the handle is display:none there.
const SBW_KEY = 'sapphire-sb-width';
const clampW = (w) => Math.max(200, Math.min(560, w));
const _savedW = parseInt(localStorage.getItem(SBW_KEY) || '', 10);
if (_savedW) document.documentElement.style.setProperty('--sb-w', clampW(_savedW) + 'px');

function initResizer(container) {
    const handle = container.querySelector('.sb-resizer');
    const sb = container.querySelector('.chat-sidebar');
    if (!handle || !sb) return;
    handle.onpointerdown = (e) => {
        e.preventDefault();
        handle.setPointerCapture(e.pointerId);
        sb.classList.add('resizing');       // kills the collapse transition mid-drag
        handle.onpointermove = (ev) => {
            const w = clampW(Math.round(sb.getBoundingClientRect().right - ev.clientX));
            document.documentElement.style.setProperty('--sb-w', w + 'px');
        };
        const done = (ev) => {
            handle.onpointermove = null; handle.onpointerup = null; handle.onpointercancel = null;
            try { handle.releasePointerCapture(ev.pointerId); } catch (_) { /* already gone */ }
            sb.classList.remove('resizing');
            const w = parseInt(getComputedStyle(sb).width, 10);
            if (w) localStorage.setItem(SBW_KEY, String(clampW(w)));
        };
        handle.onpointerup = done;
        handle.onpointercancel = done;
    };
    handle.ondblclick = () => {
        document.documentElement.style.removeProperty('--sb-w');
        localStorage.removeItem(SBW_KEY);
    };
}

export function renderSurface(container, mode) {
    if (!container || !mode) return;
    container.innerHTML = `
            <div class="chat-body${mode.cssClass ? ' ' + mode.cssClass : ''}">
                <div class="chat-main">
                    ${mode.mainPane || ''}
                    <div class="form-wrapper">
                        ${mode.formArea || ''}
                    </div>
                </div>
                <div class="sb-resizer" title="Drag to resize — double-click resets"></div>
                <aside class="chat-sidebar">
                    <div class="chat-sidebar-inner">
                        ${mode.sidebarHeader || ''}
                        ${mode.sidebarBody || ''}
                    </div>
                </aside>
                <!-- Sidebar expand tab (visible when collapsed) -->
                <button type="button" id="chat-sidebar-expand" class="sb-expand-tab" title="Show settings">&#x25C0;</button>
            </div>`;
    initResizer(container);
}
