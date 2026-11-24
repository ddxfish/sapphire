// core-widgets/mini-spotlight.js — Mini-Spotlight panel (3 random featured plugins).
// Caches the featured list internally for 5 minutes to avoid hammering the
// store endpoint. Each render samples 3 from the cache. Click a line →
// deep-link to the store detail page.

function _esc(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

let _cache = null;
let _cacheAt = 0;
const _CACHE_TTL = 5 * 60_000;  // 5 minutes

async function _getFeaturedList(ctx) {
    if (_cache && (Date.now() - _cacheAt) < _CACHE_TTL) return _cache;
    try {
        const data = await ctx.api.listStorePlugins({ featured: true, perPage: 50 });
        _cache = (data && data.items) || [];
        _cacheAt = Date.now();
        return _cache;
    } catch {
        return [];
    }
}

function _samplePicks(items, n = 3) {
    if (items.length <= n) return items.slice();
    const pool = items.slice();
    const out = [];
    while (out.length < n && pool.length) {
        const i = Math.floor(Math.random() * pool.length);
        out.push(pool[i]);
        pool.splice(i, 1);
    }
    return out;
}

export async function render(container, ctx) {
    let aborted = false;

    async function refresh() {
        const items = await _getFeaturedList(ctx);
        if (aborted) return;
        if (!items.length) {
            container.innerHTML = `
                <div class="dash-action-panel-info-line"><span class="dim">spotlight unavailable</span></div>
                <div class="dash-action-panel-info-line"><span class="dim">store unreachable or empty</span></div>
                <div class="dash-action-panel-info-line"></div>
            `;
            return;
        }
        const picks = _samplePicks(items, 3);
        container.innerHTML = picks.map(p => `
            <div class="dash-action-panel-info-line dash-spot-line" data-slug="${_esc(p.slug)}" style="cursor:pointer">
                <strong>${_esc(p.name)}</strong> <span class="dim">· ${_esc(p.author || 'Unknown')}</span>
            </div>
        `).join('');

        // Wire clicks — navigate to store detail.
        container.querySelectorAll('.dash-spot-line').forEach(el => {
            el.addEventListener('click', () => {
                window.location.hash = `#store/plugins/${encodeURIComponent(el.dataset.slug)}`;
            });
        });
    }

    refresh();
    // an open tab follows the daily refresh: ask this Sapphire again each hour (she asks the store once a day)
    const again = setInterval(() => { _cache = null; refresh(); }, 60 * 60_000);

    return {
        title: '🛍️ Spotlight',
        actions: [
            {
                icon: '↻', label: 'Refresh picks',
                onClick: () => { _cache = null; refresh(); },
            },
            {
                icon: '→', label: 'Open Store',
                onClick: () => {
                    import('/static/core/router.js').then(r => r.switchView('store'));
                },
            },
        ],
        cleanup: () => { aborted = true; clearInterval(again); },
    };
}
