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
                <aside class="chat-sidebar">
                    <div class="chat-sidebar-inner">
                        ${mode.sidebarHeader || ''}
                        ${mode.sidebarBody || ''}
                    </div>
                </aside>
                <!-- Sidebar expand tab (visible when collapsed) -->
                <button type="button" id="chat-sidebar-expand" class="sb-expand-tab" title="Show settings">&#x25C0;</button>
            </div>`;
}
