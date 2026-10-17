// sample-widgets/widgets/hello.js
//
// Minimal dashboard widget — the smallest thing that shows the contract.
// Plugin authors: copy this as a starting point.
//
// CONTRACT
//   export async function render(container, ctx)
//     container — the panel's info-line wrapper. Mutate its innerHTML.
//                 The host owns the title bar and Actions dropdown chrome.
//     ctx       — { plugin, widget_id, instance_id, size, settings, api }
//                 ctx.api gives you fetch, toast, etc. (see PLUGIN-WIDGETS.md)
//   return { title, actions, cleanup }
//     title    — string shown in the panel header
//     actions  — array of { icon, label, onClick, kind? } for the dropdown
//     cleanup  — function called when the widget unmounts. CANCEL EVERY
//                INTERVAL/EVENTSOURCE/FETCH YOU STARTED, or you'll leak.

function _esc(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export async function render(container, ctx) {
    let aborted = false;

    function paint() {
        if (aborted) return;
        const t = new Date().toLocaleTimeString();
        container.innerHTML = `
            <div class="dash-action-panel-info-line">
                <strong>Hello</strong> from ${_esc(ctx.plugin)}
            </div>
            <div class="dash-action-panel-info-line dim">it's ${_esc(t)}</div>
        `;
    }

    paint();
    const tick = setInterval(paint, 1000); // local clock — no API call needed

    return {
        title: '👋 Hello',
        actions: [
            {
                icon: '↻',
                label: 'Wave',
                onClick: () => ctx.api.toast('👋 hi', 'success'),
            },
        ],
        // CRITICAL: cancel the interval so we don't leak when the widget
        // is removed or the dashboard tab leaves.
        cleanup: () => {
            aborted = true;
            clearInterval(tick);
        },
    };
}
