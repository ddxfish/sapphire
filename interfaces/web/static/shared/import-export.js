// shared/import-export.js — Universal import/export dialogs for Sapphire
// Used by personas, prompts, toolsets, spices, etc.
import * as ui from '../ui.js';
import { setupModalClose } from './modal.js';

/**
 * Show export dialog with copy-to-clipboard and download options.
 *
 * @param {Object} opts
 * @param {string} opts.type       - Export type label (e.g. "Persona", "Prompt")
 * @param {string} opts.name       - Name of the exported item
 * @param {Object} opts.data       - The export data object
 * @param {string} opts.filename   - Download filename (e.g. "cobalt.persona.json")
 * @param {Array}  [opts.checkboxes] - Optional export checkboxes [{id, label, checked}]
 * @param {Function} [opts.buildExport] - Custom builder (receives checkbox states) → data. If omitted, opts.data is used.
 */
export function showExportDialog(opts) {
    const modal = _createOverlay();
    const checksHtml = (opts.checkboxes || []).map(c =>
        `<label class="io-option"><input type="checkbox" id="io-x-${c.id}" ${c.checked ? 'checked' : ''}> ${c.label}</label>`
    ).join('');

    modal.querySelector('.io-modal').innerHTML = `
        <div class="io-header">
            <h3>Export ${opts.type}: ${opts.name}</h3>
            <button class="btn-icon io-close">\u2715</button>
        </div>
        <div class="io-body">
            ${checksHtml}
            <div class="io-buttons">
                <button class="btn-sm" id="io-copy">Copy to Clipboard</button>
                <button class="btn-sm" id="io-download">Download File</button>
            </div>
        </div>
    `;
    _bindClose(modal);

    function getData() {
        if (opts.buildExport) {
            const states = {};
            (opts.checkboxes || []).forEach(c => {
                states[c.id] = modal.querySelector(`#io-x-${c.id}`)?.checked ?? c.checked;
            });
            return opts.buildExport(states);
        }
        return opts.data;
    }

    modal.querySelector('#io-copy').addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(JSON.stringify(getData(), null, 2));
            ui.showToast('Copied to clipboard', 'success');
        } catch { ui.showToast('Copy failed', 'error'); }
    });

    modal.querySelector('#io-download').addEventListener('click', () => {
        downloadJson(opts.filename || `${opts.name}.json`, getData());
        ui.showToast('Downloaded', 'success');
    });
}

/**
 * Show import dialog with paste/upload, overwrite checkboxes, and name collision handling.
 *
 * @param {Object} opts
 * @param {string} opts.type         - Import type label (e.g. "Persona", "Prompt")
 * @param {Array}  [opts.overwrites] - Overwrite checkboxes [{key, label}]
 * @param {Array}  opts.existingNames - Names that already exist (for collision detection)
 * @param {Function} opts.validate   - (parsed) → string|null — return error message or null if valid
 * @param {Function} opts.getName    - (parsed) → string — extract name from parsed data
 * @param {Function} opts.onImport   - async (parsed, {name, overwrites}) → void — do the import
 * @param {Function} [opts.onDone]   - () → void — called after successful import (e.g. re-render)
 */
export function showImportDialog(opts) {
    const modal = _createOverlay();
    const checksHtml = (opts.overwrites || []).map(o =>
        `<label class="io-option"><input type="checkbox" data-ow="${o.key}"> ${o.label}</label>`
    ).join('');

    modal.querySelector('.io-modal').innerHTML = `
        <div class="io-header">
            <h3>Import ${opts.type}</h3>
            <button class="btn-icon io-close">\u2715</button>
        </div>
        <div class="io-body">
            ${checksHtml ? `<div class="io-section-label">Overwrite options</div>${checksHtml}<hr class="io-divider">` : ''}
            <div class="io-buttons">
                <button class="btn-sm" id="io-paste">Paste from Clipboard</button>
                <button class="btn-sm" id="io-upload">Upload File</button>
                <input type="file" id="io-file" accept=".json" style="display:none">
            </div>
            <div id="io-status" class="io-status"></div>
        </div>
    `;
    _bindClose(modal);

    async function doImport(json) {
        const status = modal.querySelector('#io-status');
        try {
            const parsed = JSON.parse(json);

            // Validate
            const err = opts.validate?.(parsed);
            if (err) { status.textContent = err; return; }

            // Get overwrites
            const overwrites = {};
            (opts.overwrites || []).forEach(o => {
                overwrites[o.key] = modal.querySelector(`[data-ow="${o.key}"]`)?.checked ?? false;
            });

            // Name collision
            let name = opts.getName(parsed);
            if (opts.existingNames.includes(name)) {
                const newName = prompt(`"${name}" already exists. Enter a new name:`, name + '-imported');
                if (!newName?.trim()) { status.textContent = 'Import cancelled'; return; }
                name = newName.trim();
            }

            status.textContent = `Importing "${name}"...`;
            await opts.onImport(parsed, { name, overwrites });
            modal.remove();
            opts.onDone?.();
            ui.showToast(`Imported: ${name}`, 'success');
        } catch (e) { status.textContent = `Error: ${e.message}`; }
    }

    modal.querySelector('#io-paste').addEventListener('click', async () => {
        try {
            const text = await navigator.clipboard.readText();
            await doImport(text);
        } catch { modal.querySelector('#io-status').textContent = 'Clipboard read failed (check permissions)'; }
    });

    modal.querySelector('#io-upload').addEventListener('click', () => modal.querySelector('#io-file').click());
    modal.querySelector('#io-file').addEventListener('change', e => {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = () => doImport(reader.result);
        reader.readAsText(file);
    });
}

/** Download JSON data as a file. */
export function downloadJson(filename, data) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
}

// ── Internal ──

function _createOverlay() {
    const modal = document.createElement('div');
    modal.className = 'io-overlay';
    modal.innerHTML = '<div class="io-modal"></div>';
    document.body.appendChild(modal);
    return modal;
}

function _bindClose(modal) {
    setupModalClose(modal, () => modal.remove());
    modal.querySelector('.io-close')?.addEventListener('click', () => modal.remove());
}
