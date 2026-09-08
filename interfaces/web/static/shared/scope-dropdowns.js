// shared/scope-dropdowns.js
// Unified renderer for scope dropdowns across every editor that touches scopes:
//   - Chat sidebar (#view-chat Mind accordion)
//   - Trigger editor (continuity tasks via shared/trigger-editor/ai-config.js)
//   - Persona editor (views/personas.js Mind Scopes section)
//   - Future: Mind view scope creation
//
// Driven by /api/init `scope_declarations` — adding a new plugin scope means
// editing one manifest, NOT touching this file or any of the editors above.
//
// Each editor calls four functions:
//   1. renderScopeDropdowns(container, decls, settings, options)
//      → generates field HTML and wires up nav/create button handlers
//   2. await fetchScopeData(decls)
//      → Promise.allSettled fetch for every endpoint, error-isolated
//   3. await populateScopeOptions(container, decls, fetchedData, settings, options)
//      → fills <select> options, dynamically imports format_js if present
//   4. readScopeSettings(container, decls, options)
//      → reads back values into a `{ memory_scope: 'x', ... }` settings dict
//
// idPrefix differentiates editors:
//   sidebar:        'sb-'    → #sb-memory-scope
//   persona editor: 'pa-s-'  → #pa-s-memory-scope
//   trigger editor: 'ed-'    → #ed-memory-scope


// Default CSS class set matches the chat sidebar's existing `.sb-field` styling.
// Other editors override via `options.cssClasses` to keep their own look.
const DEFAULT_CSS = {
    field:        'sb-field',
    fieldRow:     'sb-field-row',
    navButton:    'sb-btn-sm sb-goto-mind',
    createButton: 'sb-btn-sm sb-create-scope',
};


/**
 * Render scope dropdown HTML into a container.
 * Generates one field per declaration. Stores declaration metadata via data-attrs
 * so populate/read can resolve back to scope keys without state on the renderer.
 *
 * @param {HTMLElement} container  Element to populate (innerHTML is replaced)
 * @param {Array}       declarations  scope_declarations from /api/init
 * @param {Object}      settings   current settings dict (initial selection)
 * @param {Object}      options
 *   @param {string}    options.idPrefix          'sb-' | 'pa-s-' | 'ed-' (default 'sb-')
 *   @param {Set}       options.enabledPlugins    Set of enabled plugin names; declarations
 *                                                with `plugin` not in set are hidden
 *   @param {Object}    options.cssClasses        override DEFAULT_CSS
 *   @param {Function}  options.onNavigate(navTarget, scopeValue)  arrow ↗ button handler
 *   @param {Function}  options.onCreateScope(scopeKey)            + button handler
 *   @param {boolean}   options.includeNoneOption default true
 */
export function renderScopeDropdowns(container, declarations, settings, options = {}) {
    if (!container) return;
    const prefix = options.idPrefix || 'sb-';
    const enabledPlugins = options.enabledPlugins || null;
    const css = { ...DEFAULT_CSS, ...(options.cssClasses || {}) };
    const includeNone = options.includeNoneOption !== false;

    let html = '';
    for (const decl of (declarations || [])) {
        // Hide if scope's plugin is disabled. Core scopes (plugin: null) always show.
        if (decl.plugin && enabledPlugins && !enabledPlugins.has(decl.plugin)) continue;

        const id = `${prefix}${decl.key}-scope`;
        const label = _esc(decl.label || decl.key);
        const settingKey = `${decl.key}_scope`;
        const currentValue = (settings && settings[settingKey]) || 'default';

        const navBtn = (options.onNavigate && decl.nav_target)
            ? `<button type="button" class="${css.navButton}" data-scope-nav="${_esc(decl.nav_target)}" data-scope-key="${_esc(decl.key)}" title="Open in Mind">&#x2197;</button>`
            : '';

        // "+" button shown only on declarations with a mind nav_target — matches the
        // legacy trigger editor UX where the bulk-create scope feature only made sense
        // for memory/goal/knowledge/people. Plugin scopes don't get "+" because creating
        // a plugin "account" isn't a single-field-entry operation.
        const showCreateBtn = options.onCreateScope && decl.nav_target;
        const createBtn = showCreateBtn
            ? `<button type="button" class="${css.createButton}" data-scope-create="${_esc(decl.key)}" title="Create new scope">+</button>`
            : '';

        // Seed with the current value so the dropdown reflects the saved setting
        // immediately, even before populateScopeOptions runs. Three cases:
        //   - current is 'default'      → mark default option selected
        //   - current is 'none'         → mark none option selected
        //   - current is something else → seed a synthetic option so value is preserved
        // populateScopeOptions() will replace the body with real fetched data afterward,
        // then re-select the current value against the real options.
        const isDefault = currentValue === 'default';
        const isNone = currentValue === 'none';
        const noneSelected = isNone ? ' selected' : '';
        const defaultSelected = isDefault ? ' selected' : '';
        const noneOptSeeded = includeNone ? `<option value="none"${noneSelected}>None</option>` : '';
        let seedOpt = `<option value="default"${defaultSelected}>default</option>`;
        if (!isDefault && !isNone && currentValue) {
            seedOpt += `<option value="${_esc(currentValue)}" selected>${_esc(currentValue)}</option>`;
        }

        const pluginAttr = decl.plugin ? ` data-scope-plugin="${_esc(decl.plugin)}"` : '';

        html += `
            <div class="${css.field}" data-scope-key="${_esc(decl.key)}"${pluginAttr}>
                <label for="${id}">${label}</label>
                <div class="${css.fieldRow}">
                    <select id="${id}">${noneOptSeeded}${seedOpt}</select>
                    ${navBtn}${createBtn}
                </div>
            </div>
        `;
    }
    container.innerHTML = html;

    // A pick the user makes while populateScopeOptions is still fetching
    // marks the select `touched`, so the populate keeps THEIR value instead of
    // reverting to the seed (and the pending debounce then persisting the old
    // one). Delegated + latched: this container may be re-rendered many times.
    // DOM-refresh hunt 2026-09-08.
    if (!container._scopeTouchBound) {
        container._scopeTouchBound = true;
        container.addEventListener('change', e => {
            if (e.target?.tagName === 'SELECT') e.target.dataset.touched = '1';
        });
    }

    // Wire nav button handlers (arrow ↗)
    if (options.onNavigate) {
        container.querySelectorAll('[data-scope-nav]').forEach(btn => {
            btn.addEventListener('click', () => {
                const navTarget = btn.dataset.scopeNav;
                const scopeKey = btn.dataset.scopeKey;
                const sel = container.querySelector(`#${prefix}${scopeKey}-scope`);
                options.onNavigate(navTarget, sel?.value || null);
            });
        });
    }

    // Wire + button handlers (create new scope)
    if (options.onCreateScope) {
        container.querySelectorAll('[data-scope-create]').forEach(btn => {
            btn.addEventListener('click', () => {
                options.onCreateScope(btn.dataset.scopeCreate);
            });
        });
    }
}


/**
 * Fetch scope data for all declarations using Promise.allSettled.
 * Failed fetches return empty arrays — one bad endpoint can't break the whole sidebar.
 *
 * @param   {Array}   declarations
 * @returns {Promise<Object>}  { [scope_key]: items[] }
 */
export async function fetchScopeData(declarations) {
    const decls = declarations || [];
    const results = await Promise.allSettled(
        decls.map(d => fetch(d.endpoint).then(r => r.ok ? r.json() : null).catch(() => null))
    );
    const data = {};
    decls.forEach((decl, i) => {
        const r = results[i];
        if (r.status === 'fulfilled' && r.value) {
            const items = r.value[decl.data_key || 'accounts'] || [];
            data[decl.key] = Array.isArray(items) ? items : [];
        } else {
            data[decl.key] = [];
        }
    });
    return data;
}


/**
 * Populate the rendered dropdowns with fetched data.
 * Each scope's options come from the data array, formatted via either:
 *   - format_js module's `formatOption(item)` function (if loaded successfully)
 *   - label_template string interpolation as fallback
 *
 * format_js modules are dynamically imported with individual try/catch — a broken
 * module logs a warning and falls back to label_template. One bad plugin can't
 * break any editor.
 *
 * @param {HTMLElement} container
 * @param {Array}       declarations
 * @param {Object}      scopeData     from fetchScopeData()
 * @param {Object}      settings      current settings (for value selection)
 * @param {Object}      options       (same shape as renderScopeDropdowns)
 */
export async function populateScopeOptions(container, declarations, scopeData, settings, options = {}) {
    if (!container) return;
    const prefix = options.idPrefix || 'sb-';
    const includeNone = options.includeNoneOption !== false;

    // Pre-load format_js modules for declarations that have them
    const formatters = {};
    for (const decl of (declarations || [])) {
        if (!decl.format_js) continue;
        try {
            const mod = await import(decl.format_js);
            if (typeof mod.formatOption === 'function') {
                formatters[decl.key] = mod.formatOption;
            } else {
                console.warn(`[scope-dropdowns] format_js for '${decl.key}' has no formatOption export`);
            }
        } catch (e) {
            console.warn(`[scope-dropdowns] Failed to load format_js for '${decl.key}':`, e);
            // Falls back to label_template
        }
    }

    for (const decl of (declarations || [])) {
        const id = `${prefix}${decl.key}-scope`;
        const sel = container.querySelector(`#${id}`);
        if (!sel) continue;  // Hidden because plugin disabled, or never rendered

        const items = scopeData[decl.key] || [];
        const settingKey = `${decl.key}_scope`;
        // A live pick (touched during the fetch) outranks the seed value.
        const current = (sel.dataset.touched && sel.value)
            || (settings && settings[settingKey]) || 'default';
        const valueField = decl.value_field || 'name';
        const formatter = formatters[decl.key];
        const labelTemplate = decl.label_template || '{name}';

        const noneOpt = includeNone ? '<option value="none">None</option>' : '';
        const optsHtml = items.map(item => {
            const value = item[valueField] != null ? String(item[valueField]) : '';
            let display;
            if (formatter) {
                try {
                    display = formatter(item);
                } catch (e) {
                    console.warn(`[scope-dropdowns] formatter failed for '${decl.key}':`, e);
                    display = _interpolate(labelTemplate, item);
                }
            } else {
                display = _interpolate(labelTemplate, item);
            }
            return `<option value="${_esc(value)}">${_esc(display)}</option>`;
        }).join('');

        sel.innerHTML = noneOpt + optsHtml;

        // Try to select the current value. If it isn't in the options list,
        // inject a 'default' option so the value is at least visible.
        // (This matches existing behavior in chat.js where 'default' was always
        // expected to be in the API response for memory/knowledge/goals/people.)
        if (current && [...sel.options].some(o => o.value === current)) {
            sel.value = current;
        } else if (current && current !== 'none') {
            // Inject the missing value as a visible option so the user can see
            // their saved selection even if the API doesn't return it.
            const synthetic = document.createElement('option');
            synthetic.value = current;
            synthetic.textContent = current;
            sel.appendChild(synthetic);
            sel.value = current;
        } else {
            sel.value = 'none';
        }
    }
}


/**
 * Read all scope dropdown values into a settings dict.
 * Only includes scopes whose dropdowns are present in the container.
 * (Hidden plugin scopes don't get written, which is the correct behavior —
 * we don't want to clobber a saved value just because the plugin happens to be
 * disabled in this session.)
 *
 * @param   {HTMLElement} container
 * @param   {Array}       declarations
 * @param   {Object}      options    (same shape as renderScopeDropdowns)
 * @returns {Object}      { memory_scope: 'work', email_scope: 'home', ... }
 */
export function readScopeSettings(container, declarations, options = {}) {
    if (!container) return {};
    const prefix = options.idPrefix || 'sb-';
    const result = {};
    for (const decl of (declarations || [])) {
        const id = `${prefix}${decl.key}-scope`;
        const sel = container.querySelector(`#${id}`);
        if (sel) {
            result[`${decl.key}_scope`] = sel.value || 'default';
        }
    }
    return result;
}


/**
 * Read scope dropdown values from a container by DOM discovery — finds every
 * element tagged with `data-scope-key` (added by renderScopeDropdowns) and reads
 * its child select. Doesn't need the declarations list to be in scope.
 *
 * Useful when the read-site is in a different function than the declarations
 * (e.g. `readAIConfig` in the trigger editor runs long after `fetchAIConfigData`).
 *
 * @param   {HTMLElement} container
 * @param   {Object}      opts
 *   @param {string}      opts.missingValue  default value when select is empty (default 'default')
 * @returns {Object}      { memory_scope: 'work', ... }
 */
export function readScopeSettingsFromDom(container, opts = {}) {
    if (!container) return {};
    const missingValue = opts.missingValue ?? 'default';
    const result = {};
    container.querySelectorAll('[data-scope-key]').forEach(el => {
        const key = el.dataset.scopeKey;
        const sel = el.querySelector('select');
        if (key && sel) {
            result[`${key}_scope`] = sel.value || missingValue;
        }
    });
    return result;
}


// ─────────────────────────── Helpers ───────────────────────────

/** Interpolate a `{field}` template string with values from an object. */
function _interpolate(template, item) {
    return String(template).replace(/\{(\w+)\}/g, (_, key) => {
        const v = item[key];
        return v !== undefined && v !== null ? String(v) : '';
    });
}

/** HTML-escape a string for safe interpolation into option values/text. */
function _esc(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
