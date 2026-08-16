// views/people.js - Mind › People. Contacts the AI learns about. Own scope
// domain (people_scope). Extracted from the mind.js monolith.
import { renderSectionHeader, bindSectionHeader } from '../shared/section-header.js';
import { helpPills } from '../features/video-link.js';
import { renderScopeSidebar, bindScopeSidebar } from '../shared/scope-sidebar.js';
import { listScopes } from '../shared/scope-api.js';
import { MIND_TABS, csrfHeaders, escHtml, escAttr, scopeForChatTab, subscribeMindDomain } from '../shared/mind-common.js';
import { showExportDialog, showImportDialog } from '../shared/import-export.js';
import { setupModalClose } from '../shared/modal.js';
import * as ui from '../ui.js';

const SCOPE_KEY = 'people_scope';
const DOMAIN = 'people';
const SCOPE_ENDPOINT = '/api/knowledge/people/scopes';

let container = null;
let scope = 'default';
let scopes = [];
let unsub = null;

export default {
    init(el) { container = el; },
    async show() {
        if (!unsub) unsub = subscribeMindDomain(DOMAIN, () => scope, () => container?.offsetParent !== null, renderPeople);
        if (window._mindScope) { scope = window._mindScope; delete window._mindScope; }
        else { const s = await scopeForChatTab(SCOPE_KEY); if (s) scope = s; }
        delete window._mindTab;
        scopes = await listScopes(SCOPE_ENDPOINT);
        render();
    },
    hide() { if (unsub) { unsub(); unsub = null; } }
};

function content() { return container?.querySelector('#people-content'); }

function render() {
    if (!container) return;
    container.innerHTML = `
        ${renderSectionHeader({ tabs: MIND_TABS, active: 'people', help: helpPills('People', { video: 'I3g3tzukpV0', doc: 'PEOPLE.md', inline: true }), status: 'Contacts Sapphire learns about — searchable by name, relationship, or notes.' })}
        <div class="two-panel">
            ${renderScopeSidebar(scopes, scope)}
            <div class="panel-right">
                <div class="view-body view-scroll" id="people-content"></div>
            </div>
        </div>`;
    bindSectionHeader(container);
    bindScopeSidebar(container, {
        onScopeChange: (s) => { scope = s; render(); },
        onChanged: async (s) => { scope = s || 'default'; scopes = await listScopes(SCOPE_ENDPOINT); render(); },
    });
    renderPeople();
}

async function renderPeople() {
    const el = content();
    if (!el) return;
    const resp = await fetch(`/api/knowledge/people?scope=${encodeURIComponent(scope)}`);
    if (!resp.ok) { el.innerHTML = '<div class="mind-empty">Failed to load</div>'; return; }
    const data = await resp.json();
    const people = data.people || [];

    el.innerHTML = `
        <div class="mind-toolbar">
            <button class="mind-btn" id="mind-add-person">+ Add Person</button>
            <button class="mind-btn" id="mind-import-vcf">Import VCF</button>
            <button class="mind-btn" id="mind-export-people">Export</button>
            <button class="mind-btn" id="mind-import-people">Import</button>
            <input type="file" id="mind-vcf-input" accept=".vcf" style="display:none">
        </div>
        ${people.length ? `<div class="mind-people-grid">
            ${people.map(p => `
                <div class="mind-person-card" data-id="${p.id}">
                    <div class="mind-person-name">${escHtml(p.name)}${p.email_whitelisted ? ' <span title="Email allowed" style="font-size:0.75rem">&#x1F4E7;</span>' : ''}${p.call_whitelisted ? ' <span title="Calls allowed" style="font-size:0.75rem">&#x1F4DE;</span>' : ''}</div>
                    ${p.relationship ? `<div class="mind-person-rel">${escHtml(p.relationship)}</div>` : ''}
                    <div class="mind-person-details">
                        ${p.phone ? `<div>&#x1F4DE; ${escHtml(p.phone)}</div>` : ''}
                        ${p.email ? `<div>&#x2709; ${escHtml(p.email)}</div>` : ''}
                        ${p.address ? `<div>&#x1F4CD; ${escHtml(p.address)}</div>` : ''}
                    </div>
                    ${p.notes ? `<div class="mind-person-notes">${escHtml(p.notes)}</div>` : ''}
                    <div class="mind-person-actions">
                        <button class="mind-btn-sm mind-edit-person" data-id="${p.id}">Edit</button>
                        <button class="mind-btn-sm mind-del-person" data-id="${p.id}">Delete</button>
                    </div>
                </div>
            `).join('')}
        </div>` : '<div class="mind-empty">No contacts saved</div>'}
    `;

    el.querySelector('#mind-add-person')?.addEventListener('click', () => showPersonModal());

    const vcfInput = el.querySelector('#mind-vcf-input');
    el.querySelector('#mind-import-vcf')?.addEventListener('click', () => vcfInput?.click());
    vcfInput?.addEventListener('change', async (e) => {
        const file = e.target.files?.[0];
        if (!file) return;
        const form = new FormData();
        form.append('file', file);
        form.append('scope', scope);
        try {
            const resp = await fetch('/api/knowledge/people/import-vcf', { method: 'POST', headers: csrfHeaders(), body: form });
            if (!resp.ok) throw new Error('Upload failed');
            const result = await resp.json();
            let msg = `Imported ${result.imported} of ${result.total_in_file} contacts`;
            if (result.skipped_count > 0) {
                msg += `\nSkipped ${result.skipped_count} duplicates:`;
                result.skipped.forEach(s => { msg += `\n  - ${s}`; });
                if (result.skipped_count > result.skipped.length) msg += `\n  ... and ${result.skipped_count - result.skipped.length} more`;
            }
            ui.showToast(msg, result.imported > 0 ? 'success' : 'info');
            await renderPeople();
        } catch (err) { ui.showToast('Import failed: ' + err.message, 'error'); }
        vcfInput.value = '';
    });

    el.querySelectorAll('.mind-edit-person').forEach(btn => {
        btn.addEventListener('click', () => {
            const p = people.find(x => x.id === parseInt(btn.dataset.id));
            if (p) showPersonModal(p);
        });
    });

    el.querySelectorAll('.mind-del-person').forEach(btn => {
        btn.addEventListener('click', async () => {
            if (!confirm('Delete this contact?')) return;
            try {
                const resp = await fetch(`/api/knowledge/people/${btn.dataset.id}`, { method: 'DELETE', headers: csrfHeaders() });
                if (resp.ok) { ui.showToast('Deleted', 'success'); await renderPeople(); }
            } catch (e) { ui.showToast('Failed', 'error'); }
        });
    });

    el.querySelector('#mind-export-people')?.addEventListener('click', async () => {
        try {
            const resp = await fetch(`/api/knowledge/people/export?scope=${encodeURIComponent(scope)}`);
            if (!resp.ok) throw new Error('Export failed');
            const data = await resp.json();
            showExportDialog({
                type: 'People',
                name: `${scope} (${data.count})`,
                filename: `people-${scope}.json`,
                data,
            });
        } catch (e) { ui.showToast(e.message, 'error'); }
    });

    el.querySelector('#mind-import-people')?.addEventListener('click', () => {
        showImportDialog({
            type: 'People',
            existingNames: [],
            validate: (d) => {
                if (d.entries && Array.isArray(d.entries)) return null;
                return 'Invalid format: needs entries array';
            },
            getName: (d) => d.scope || scope,
            onImport: async (data) => {
                const resp = await fetch('/api/knowledge/people/import', {
                    method: 'POST',
                    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                    body: JSON.stringify({ entries: data.entries, scope }),
                });
                if (!resp.ok) throw new Error('Import failed');
                const result = await resp.json();
                ui.showToast(`Imported ${result.imported} contacts, ${result.skipped} duplicates skipped`, 'success');
            },
            onDone: async () => { await renderPeople(); },
        });
    });
}

function showPersonModal(person = null) {
    document.querySelector('.mind-modal-overlay')?.remove();
    const overlay = document.createElement('div');
    overlay.className = 'pr-modal-overlay mind-modal-overlay';
    overlay.innerHTML = `
        <div class="pr-modal">
            <div class="pr-modal-header">
                <h3>${person ? 'Edit' : 'Add'} Contact</h3>
                <button class="mind-btn-sm mind-modal-close">&#x2715;</button>
            </div>
            <div class="pr-modal-body">
                <div class="mind-form">
                    <input type="text" id="mp-name" placeholder="Name *" value="${escAttr(person?.name || '')}">
                    <input type="text" id="mp-relationship" placeholder="Relationship" value="${escAttr(person?.relationship || '')}">
                    <input type="text" id="mp-phone" placeholder="Phone" value="${escAttr(person?.phone || '')}">
                    <input type="text" id="mp-email" placeholder="Email" value="${escAttr(person?.email || '')}">
                    <input type="text" id="mp-address" placeholder="Address" value="${escAttr(person?.address || '')}">
                    <textarea id="mp-notes" placeholder="Notes" rows="3">${escHtml(person?.notes || '')}</textarea>
                    <label style="display:flex;align-items:center;gap:8px;font-size:0.8125rem;color:var(--text-muted);cursor:pointer">
                        <input type="checkbox" id="mp-email-whitelist" ${person?.email_whitelisted ? 'checked' : ''}> Allow AI to send email
                    </label>
                    <label style="display:flex;align-items:center;gap:8px;font-size:0.8125rem;color:var(--text-muted);cursor:pointer">
                        <input type="checkbox" id="mp-call-whitelist" ${person?.call_whitelisted ? 'checked' : ''}> Allow AI to call
                    </label>
                    <button class="mind-btn" id="mp-save">${person ? 'Update' : 'Save'}</button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    overlay.querySelector('.mind-modal-close').addEventListener('click', () => overlay.remove());
    setupModalClose(overlay, () => overlay.remove());

    overlay.querySelector('#mp-save').addEventListener('click', async () => {
        const name = overlay.querySelector('#mp-name').value.trim();
        if (!name) { ui.showToast('Name is required', 'error'); return; }
        const body = {
            name,
            relationship: overlay.querySelector('#mp-relationship').value.trim(),
            phone: overlay.querySelector('#mp-phone').value.trim(),
            email: overlay.querySelector('#mp-email').value.trim(),
            address: overlay.querySelector('#mp-address').value.trim(),
            notes: overlay.querySelector('#mp-notes').value.trim(),
            email_whitelisted: overlay.querySelector('#mp-email-whitelist').checked,
            call_whitelisted: overlay.querySelector('#mp-call-whitelist').checked,
            scope,
        };
        if (person?.id) body.id = person.id;
        try {
            const resp = await fetch('/api/knowledge/people', {
                method: 'POST',
                headers: csrfHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(body)
            });
            if (resp.ok) { overlay.remove(); ui.showToast(person ? 'Updated' : 'Saved', 'success'); await renderPeople(); }
            else { const err = await resp.json(); ui.showToast(err.detail || 'Failed', 'error'); }
        } catch (e) { ui.showToast('Failed', 'error'); }
    });
}
