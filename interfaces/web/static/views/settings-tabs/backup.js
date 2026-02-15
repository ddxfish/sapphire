// settings-tabs/backup.js - Backup management
import * as ui from '../../ui.js';

let backups = { daily: [], weekly: [], monthly: [], manual: [] };
let expanded = {};

export default {
    id: 'backup',
    name: 'Backup',
    icon: '\uD83D\uDCBE',
    description: 'Automatic and manual backups of user data',
    keys: ['BACKUPS_ENABLED', 'BACKUPS_KEEP_DAILY', 'BACKUPS_KEEP_WEEKLY', 'BACKUPS_KEEP_MONTHLY', 'BACKUPS_KEEP_MANUAL'],

    render(ctx) {
        return `
            ${ctx.renderFields(this.keys)}

            <div class="backup-hero">
                <button class="backup-now-btn" id="backup-now">Backup Now</button>
                <div class="backup-stats" id="backup-stats"></div>
            </div>

            <div class="backup-section-divider">
                <h4 style="margin:0 0 10px;font-size:var(--font-sm)">Backup Files</h4>
                <div id="backup-lists"></div>
            </div>
        `;
    },

    async attachListeners(ctx, el) {
        await this.loadBackups(el);

        el.querySelector('#backup-now')?.addEventListener('click', async () => {
            const btn = el.querySelector('#backup-now');
            btn.disabled = true;
            btn.textContent = 'Creating...';
            try {
                const res = await fetch('/api/backup/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ type: 'manual' })
                });
                if (res.ok) {
                    const data = await res.json();
                    ui.showToast(`Backup created: ${data.filename}`, 'success');
                    await this.loadBackups(el);
                } else {
                    ui.showToast('Backup failed', 'error');
                }
            } catch (e) { ui.showToast('Backup failed', 'error'); }
            finally { btn.disabled = false; btn.textContent = 'Backup Now'; }
        });
    },

    async loadBackups(el) {
        try {
            const res = await fetch('/api/backup/list');
            if (res.ok) {
                const data = await res.json();
                backups = data.backups || {};
            }
        } catch {}
        this.renderBackups(el);
    },

    renderBackups(el) {
        const lists = el.querySelector('#backup-lists');
        const stats = el.querySelector('#backup-stats');
        if (!lists) return;

        let totalSize = 0, totalCount = 0;
        for (const type of ['daily', 'weekly', 'monthly', 'manual']) {
            for (const b of (backups[type] || [])) {
                totalSize += b.size || 0;
                totalCount++;
            }
        }

        if (stats) stats.textContent = `${totalCount} backups \u00B7 ${fmtSize(totalSize)}`;

        lists.innerHTML = ['daily', 'weekly', 'monthly', 'manual'].map(type => {
            const items = backups[type] || [];
            const isOpen = expanded[type];
            return `
                <div class="backup-type-section">
                    <div class="backup-type-header" data-type="${type}">
                        <span class="accordion-arrow" style="transform:${isOpen ? 'rotate(90deg)' : 'none'}">\u25B6</span>
                        <span class="backup-type-title">${type[0].toUpperCase() + type.slice(1)}</span>
                        <span class="backup-type-count">${items.length}</span>
                    </div>
                    <div class="backup-type-body" style="display:${isOpen ? 'block' : 'none'}">
                        ${items.length ? items.map(b => `
                            <div class="backup-item" data-filename="${esc(b.filename)}">
                                <span class="backup-item-date">${b.date} ${b.time}</span>
                                <span class="backup-item-size">${fmtSize(b.size)}</span>
                                <div class="backup-item-actions">
                                    <a class="btn-icon backup-dl" href="/api/backup/download/${encodeURIComponent(b.filename)}" download title="Download">\u2B07</a>
                                    <button class="btn-icon danger backup-del" data-filename="${esc(b.filename)}" title="Delete">\u2715</button>
                                </div>
                            </div>
                        `).join('') : '<div class="backup-empty">No backups</div>'}
                    </div>
                </div>
            `;
        }).join('');

        // Accordion toggles
        lists.querySelectorAll('.backup-type-header').forEach(header => {
            header.addEventListener('click', () => {
                const type = header.dataset.type;
                expanded[type] = !expanded[type];
                const body = header.nextElementSibling;
                const arrow = header.querySelector('.accordion-arrow');
                body.style.display = expanded[type] ? 'block' : 'none';
                arrow.style.transform = expanded[type] ? 'rotate(90deg)' : 'none';
            });
        });

        // Delete buttons
        lists.querySelectorAll('.backup-del').forEach(btn => {
            btn.addEventListener('click', async () => {
                const filename = btn.dataset.filename;
                if (!confirm(`Delete ${filename}?`)) return;
                try {
                    const res = await fetch(`/api/backup/delete/${encodeURIComponent(filename)}`, { method: 'DELETE' });
                    if (res.ok) {
                        ui.showToast('Deleted', 'success');
                        await this.loadBackups(el);
                    }
                } catch { ui.showToast('Delete failed', 'error'); }
            });
        });
    }
};

function fmtSize(bytes) {
    if (!bytes) return '0 B';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function esc(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
