// shared/scene-picker.js - Win11-style scene background picker.
// DRY: mounted in BOTH the persona Scene accordion and the chat sidebar modal.
// mountScenePicker(container, { current, onSelect }) -> { reload }.
//   onSelect(name) fires with the chosen scene name, or '' for None.
import { listBackgrounds, uploadBackground, deleteBackground, backgroundThumbUrl } from './backgrounds-api.js';
import { escapeHtml } from './modal.js';

const SEARCH_THRESHOLD = 12;  // progressive disclosure: search only when the library is big

export async function mountScenePicker(container, opts = {}) {
    const onSelect = opts.onSelect || (() => {});
    let current = opts.current || '';
    let scenes = [];
    let filter = '';

    async function load() {
        try {
            const data = await listBackgrounds();
            scenes = (data && data.backgrounds) || [];
        } catch {
            scenes = [];
        }
        render();
    }

    function render() {
        const showSearch = scenes.length > SEARCH_THRESHOLD;
        const q = filter.trim().toLowerCase();
        const shown = q ? scenes.filter(s => s.name.includes(q)) : scenes;

        const tiles = shown.map(s => `
            <div class="scene-tile${s.name === current ? ' selected' : ''}" data-name="${escapeHtml(s.name)}" title="${escapeHtml(s.name)}">
                <img loading="lazy" src="${backgroundThumbUrl(s.name)}" alt="">
                <span class="scene-tile-name">${escapeHtml(s.name)}</span>
                <button class="scene-tile-del" data-del="${escapeHtml(s.name)}" title="Delete scene">&times;</button>
            </div>`).join('');

        container.innerHTML = `
            <div class="scene-picker">
                ${showSearch ? `<input type="text" class="scene-search" placeholder="Search scenes…" value="${escapeHtml(filter)}">` : ''}
                <div class="scene-grid">
                    <div class="scene-tile scene-none${!current ? ' selected' : ''}" data-name="" title="No background (default)">
                        <div class="scene-none-inner">None</div>
                    </div>
                    ${tiles}
                    <div class="scene-tile scene-upload" title="Upload a scene">
                        <div class="scene-upload-inner">+ Upload</div>
                        <input type="file" class="scene-upload-input" accept="image/*" hidden>
                    </div>
                </div>
            </div>`;
        bind();
    }

    function bind() {
        const root = container.querySelector('.scene-picker');
        if (!root) return;

        const search = root.querySelector('.scene-search');
        if (search) {
            search.addEventListener('input', e => {
                filter = e.target.value;
                const pos = e.target.selectionStart;
                render();
                const s2 = container.querySelector('.scene-search');
                if (s2) { s2.focus(); s2.setSelectionRange(pos, pos); }
            });
        }

        root.querySelectorAll('.scene-tile').forEach(tile => {
            if (tile.classList.contains('scene-upload')) return;
            tile.addEventListener('click', e => {
                if (e.target.closest('.scene-tile-del')) return;
                current = tile.dataset.name || '';
                onSelect(current);
                render();
            });
        });

        root.querySelectorAll('.scene-tile-del').forEach(btn => {
            btn.addEventListener('click', async e => {
                e.stopPropagation();
                const name = btn.dataset.del;
                if (!name || !confirm(`Delete scene "${name}"?`)) return;
                try {
                    await deleteBackground(name);
                    if (current === name) { current = ''; onSelect(''); }
                    await load();
                } catch (err) { alert('Delete failed: ' + (err.message || err)); }
            });
        });

        const up = root.querySelector('.scene-upload');
        const input = root.querySelector('.scene-upload-input');
        if (up && input) {
            up.addEventListener('click', () => input.click());
            input.addEventListener('change', async e => {
                const file = e.target.files[0];
                input.value = '';
                if (!file) return;
                const suggested = file.name.replace(/\.[^.]+$/, '');
                const name = prompt('Name this scene (one word):', suggested);
                if (name === null) return;
                await doUpload(name, file, false);
            });
        }
    }

    async function doUpload(name, file, overwrite) {
        try {
            const res = await uploadBackground(name, file, overwrite);
            current = (res && res.name) || name;
            onSelect(current);
            await load();
        } catch (err) {
            if (!overwrite && /already exists/i.test(err.message || '')) {
                if (confirm(`Scene "${name}" exists. Overwrite?`)) {
                    await doUpload(name, file, true);
                }
            } else {
                alert('Upload failed: ' + (err.message || err));
            }
        }
    }

    await load();
    return { reload: load };
}
