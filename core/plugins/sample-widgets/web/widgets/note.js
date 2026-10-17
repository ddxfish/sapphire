// sample-widgets/widgets/note.js
//
// Demonstrates: settings_schema (text + color), multi-size adaptation,
// multi-instance, namespaced CSS, the auto-rendered settings modal.
//
// User flow:
//   1. Add "Pinned Note" from + Add. (Shipped at default size 1x2.)
//   2. Click Actions ▾ → ⚙ Settings... → modal opens
//   3. Edit text / color, Save → widget re-renders with new content
//
// Settings come in via ctx.settings — already the merged result of the
// user's saved values + your defaults. You don't need to call any API
// to load them.

const COLOR_MAP = {
    blue:   '#4a9eff',
    green:  '#22c97a',
    orange: '#f5a623',
    pink:   '#e066c5',
    white:  '#f0f4ff',
};

function _esc(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export async function render(container, ctx) {
    const text = (ctx.settings?.text || '').trim() || '(empty)';
    const colorKey = ctx.settings?.color || 'blue';
    const color = COLOR_MAP[colorKey] || COLOR_MAP.blue;

    // Larger sizes get larger text. The widget adapts to whatever size
    // the user picked via the resize pills in edit mode.
    const fontSize = ctx.size === '1x4' ? '20px'
                   : ctx.size === '1x2' ? '15px'
                   : '13px';

    // Namespacing pro-tip: prefix your custom classes with your plugin
    // name so they can never collide with another plugin's CSS. We use
    // inline styles here for brevity.
    container.innerHTML = `
        <div class="dash-action-panel-info-line"
             style="color:${color};font-size:${fontSize};line-height:1.4;white-space:pre-wrap">
            ${_esc(text)}
        </div>
    `;

    return {
        title: '📝 ' + (ctx.size === '1x1' ? 'Note' : 'Pinned Note'),
        actions: [
            // Note: when settings_schema is declared in the manifest, the
            // host ALSO auto-appends a "⚙ Settings..." action. You don't
            // need to wire that yourself.
            {
                icon: '✏',
                label: 'Edit (alias for Settings)',
                onClick: () => ctx.api.openWidgetSettings?.(ctx.instance_id),
            },
        ],
        // Nothing async to clean up — but always return a cleanup function
        // anyway. Empty is fine; missing causes a console warning.
        cleanup: () => {},
    };
}
