// Game Room — the Library (Choose Game), a Surface consumer like the room:
// wide tiles in the main pane, library sidebar (search, type filter, room
// settings) on the right. Games come from the core registry
// (capabilities.games → GET /api/games), room-mountable only. Play opens the
// game's newest session in room.js — sessions are mode-tagged chats.
// Deep links: #app-game-room/<game> (bookmarkable, game-level).

import { renderSurface } from '/static/surface/surface.js';
import { accordionHtml, initAccordions } from '/static/shared/accordion.js';
import * as ui from '/static/ui.js';

let room = null;   // ./room.js, loaded with boot-version (same policy as views)
let storyRoom = null;   // ./story-room.js — lazy, only when a story tile opens
let _vaultOpen = false; // refreshed by renderLibrary; gates the 🗝 Private buttons
let _root = null;
let _cssLink = null;
let _games = [];
let _roomGames = [];
let _stories = [];
let _sessions = [];
let _search = '';
let _genre = 'all';

const LIB_SIDEBAR_KEY = 'sapphire-game-lib-sidebar';

// Any click outside a split-Play control closes open Play menus (the carat
// and menu items stopPropagation, so this only sees genuine outside clicks).
// Module scope = bound once; a no-op querySelectorAll outside the library.
document.addEventListener('click', () =>
    document.querySelectorAll('.gr-play-menu').forEach(m => { m.style.display = 'none'; }));
const bootV = () => document.querySelector('meta[name="boot-version"]')?.content || '';
const csrfTok = () => document.querySelector('meta[name="csrf-token"]')?.content || '';

// Render sequence: module state (_root, room, storyRoom) is shared across
// renders of this memoized module. A superseded render's teardown must
// no-op once a newer render owns that state — without the guard, the loser
// of a rapid nav race killed the winner (post-fix review 2026-08-05).
let _renderSeq = 0;

export async function render(container) {
    const seq = ++_renderSeq;
    _root = container;
    if (!room) room = await import(`./room.js?v=${bootV()}`);
    if (!_cssLink) {
        _cssLink = document.createElement('link');
        _cssLink.rel = 'stylesheet';
        _cssLink.href = '/plugin-web/game-room/app/game-room.css?v=' + bootV();
        document.head.appendChild(_cssLink);
    }
    // Deep links: #app-game-room/<game> or /story:<slug> — stash BEFORE
    // renderLibrary resets the hash to the base.
    const deepStory = location.hash.match(/^#app-game-room\/story:([a-z0-9][a-z0-9_-]{0,64})/);
    const deep = deepStory ? null : location.hash.match(/^#app-game-room\/([a-z0-9][a-z0-9_-]{0,32})/);
    await renderLibrary();
    if (deepStory) {
        const s = _stories.find(x => x.slug === deepStory[1]);
        if (s) openStory(s);
    } else if (deep) {
        const g = _roomGames.find(x => x.id === deep[1]);
        if (g) {
            room.openRoom(_root, g, null, { back: renderLibrary, games: _roomGames })
                .catch(e => console.error('[GameRoom] deep link failed', deep[1], e));
        }
    }
    // Per-render teardown for the host: only the CURRENT render may clean up.
    return () => { if (seq === _renderSeq) cleanup(); };
}

export function cleanup() {
    if (room) room.close();
    if (storyRoom) storyRoom.close();   // returns the borrowed organs home
    if (_cssLink) { _cssLink.remove(); _cssLink = null; }
    _root = null;
    // Leaving the Game Room with a game session still ACTIVE: hand Chat back
    // to the chats — most recent real chat, else default (Krem 2026-08-03:
    // "Titanic active when it shouldn't be visible"). Scoped to THIS tab's
    // deliberate exit, so a phone mid-game is never yanked by a desktop that
    // merely opens Chat. Fire-and-forget: Chat works either way.
    const roomMod = room;
    (async () => {
        try {
            if (!roomMod) return;
            const r = await fetch('/api/chats', { headers: { 'X-CSRF-Token': csrfTok() } });
            if (!r.ok) return;
            const d = await r.json();
            const act = (d.chats || []).find(c => c.name === d.active_chat);
            if (!act || (act.mode || act.settings?.mode) !== 'game') return;
            const target = (d.chats || []).find(c =>
                !(c.mode || c.settings?.mode) && !c.archived && !c.private_chat)?.name || 'default';
            await roomMod.activateSession(target);
        } catch (e) { /* picker hides game chats regardless */ }
    })();
}

async function renderLibrary() {
    room.close();
    if (storyRoom) storyRoom.close();
    let roomCfg = {};
    try {
        const res = await fetch('/api/games', { headers: { 'X-CSRF-Token': csrfTok() } });
        if (res.ok) _games = ((await res.json()).games || []);
    } catch (e) {
        console.warn('[GameRoom] games registry fetch failed', e);
    }
    try { _sessions = await room.listSessions(); } catch (e) { _sessions = []; }
    // Play Private is offered only while the vault is OPEN — closed or
    // absent, the library looks exactly like pre-v1.3 (the second universe
    // only exists once the door's unlocked).
    _vaultOpen = false;
    try {
        const st = await (await fetch('/api/status')).json();
        _vaultOpen = !!(st?.vault?.exists && st?.vault?.unlocked);
    } catch { /* no status = no private button, fail quiet */ }
    try {
        const res = await fetch('/api/plugin/game-room/room/config', { headers: { 'X-CSRF-Token': csrfTok() } });
        if (res.ok) roomCfg = (await res.json()).config || {};
    } catch (e) { /* sidebar shows blank name */ }
    // Stories are chat-gear games — scanned server-side across story packs
    // (story-samples plugin, user/story_presets/, any plugin's stories/)
    try {
        const res = await fetch('/api/plugin/game-room/story/status', { headers: { 'X-CSRF-Token': csrfTok() } });
        _stories = res.ok ? ((await res.json()).stories || []) : [];
    } catch (e) { _stories = []; }

    if (!_root) return;
    history.replaceState(null, '', '#app-game-room');
    _roomGames = _games.filter(g => (g.surfaces || []).includes('room'));
    const esc = room.esc;

    const genres = [...new Set([
        ..._roomGames.map(g => (g.genre || 'other')),
        ...(_stories.length ? ['story'] : []),
    ])].sort();

    renderSurface(_root, {
        id: 'game-lib',
        cssClass: 'surface-library',
        mainPane: `
            <div class="gr-lib">
                <div class="gr-header">
                    <h1><span class="gr-dice">\u{1F3B2}</span> Game Room</h1>
                    <span class="gr-sub">pick a game &mdash; every session is a chat, the chat is the save</span>
                </div>
                <div class="gr-shelf" id="gr-shelf"></div>
            </div>`,
        formArea: '',
        sidebarHeader: `
            <div class="sb-chat-header">
                <span class="gr-lib-sb-title">\u{1F3B2} Games</span>
                <button type="button" id="gr-lib-collapse" class="sb-icon-btn sb-collapse-btn" title="Hide sidebar">&#x25B6;</button>
            </div>`,
        sidebarBody: `
            <div class="sidebar-section">
                <div class="sb-field">
                    <label>find</label>
                    <input type="text" id="gr-lib-search" placeholder="Search games..." autocomplete="off">
                </div>
                <div class="sb-field sb-field-stack">
                    <label>type</label>
                    <div class="sb-toggles gr-genre-chips" id="gr-genre-chips">
                        <button type="button" class="sb-toggle" data-genre="all">All</button>
                        ${genres.map(g => `<button type="button" class="sb-toggle" data-genre="${esc(g)}">${esc(g)}</button>`).join('')}
                    </div>
                </div>
            </div>
            ${accordionHtml({
                id: 'lib:room', title: 'Room', icon: '\u{1F6CB}', open: true,
                content: `
                    <div class="sb-field">
                        <label>player name</label>
                        <input type="text" id="gr-player-name" maxlength="40" placeholder="Krem" value="${esc(roomCfg.player_name || '')}">
                    </div>
                    <div class="gr-seat-note">Your seat name in new sessions.</div>`,
            })}`,
    });

    // Sidebar collapse — own preference key, chat's CSS
    const sidebar = _root.querySelector('.chat-sidebar');
    if (localStorage.getItem(LIB_SIDEBAR_KEY) === 'collapsed') sidebar.classList.add('collapsed');
    const toggleSb = () => {
        const collapsed = sidebar.classList.toggle('collapsed');
        localStorage.setItem(LIB_SIDEBAR_KEY, collapsed ? 'collapsed' : 'expanded');
    };
    _root.querySelector('#gr-lib-collapse').onclick = toggleSb;
    _root.querySelector('#chat-sidebar-expand').onclick = toggleSb;

    initAccordions(_root.querySelector('.chat-sidebar-inner'), 'game-lib');

    // Search + type filter drive the shelf live
    _root.querySelector('#gr-lib-search').addEventListener('input', (e) => {
        _search = e.target.value.trim();
        paintShelf();
    });
    const chips = _root.querySelector('#gr-genre-chips');
    const paintChips = () => chips.querySelectorAll('.sb-toggle').forEach(c =>
        c.classList.toggle('active', c.dataset.genre === _genre));
    chips.addEventListener('click', (e) => {
        const chip = e.target.closest('.sb-toggle');
        if (!chip) return;
        _genre = chip.dataset.genre;
        paintChips();
        paintShelf();
    });
    if (!genres.includes(_genre) && _genre !== 'all') _genre = 'all';
    paintChips();

    // Player name — room-wide, feeds new sessions' seat name
    const nameInput = _root.querySelector('#gr-player-name');
    nameInput.addEventListener('change', async () => {
        try {
            await fetch('/api/plugin/game-room/room/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfTok() },
                body: JSON.stringify({ config: { player_name: nameInput.value } }),
            });
        } catch (e) { console.warn('[GameRoom] player name save failed', e); }
    });

    paintShelf();
}

function paintShelf() {
    const shelf = _root?.querySelector('#gr-shelf');
    if (!shelf) return;
    const esc = room.esc;
    const counts = {};
    for (const s of _sessions) {
        const gid = s.settings?.game_id;
        if (gid) counts[gid] = (counts[gid] || 0) + 1;   // story:<slug> keys included
    }
    const q = _search.toLowerCase();
    const shown = _roomGames.filter(g =>
        (_genre === 'all' || (g.genre || 'other') === _genre)
        && (!q || `${g.title || ''} ${g.desc || ''} ${g.id} ${g.players || ''}`.toLowerCase().includes(q)));
    const shownStories = _stories.filter(s =>
        (_genre === 'all' || _genre === 'story')
        && (!q || `${s.title || ''} ${s.description || ''} ${s.slug} ${(s.tags || []).join(' ')}`.toLowerCase().includes(q)));

    // One compact stats line always visible; "More Info ▾" beside it expands
    // the tile for the pack-authored facts + full details (Krem 2026-08-16 —
    // moved off the button stack into the body, left of the stats).
    const artFmt = kb => kb >= 1024 ? (kb / 1024).toFixed(1) + 'MB' : kb + 'KB';
    // Split Play: one blue pill, Play is the default, ▾ drops Play Private.
    // Vault closed = plain Play, no carat — the library looks exactly like
    // pre-v1.3 until the door's unlocked (same rule as before, fewer buttons).
    const playCtl = (privTitle) => _vaultOpen ? `
        <div class="gr-play-split">
          <button class="pk-btn pk-btn-primary gr-play">Play</button>
          <button class="pk-btn pk-btn-primary gr-play-caret" title="More ways to play">&#x25BE;</button>
          <div class="gr-play-menu" style="display:none">
            <button class="gr-play-priv" title="${privTitle}">\u{1F5DD} Play Private</button>
          </div>
        </div>`
        : `<button class="pk-btn pk-btn-primary gr-play">Play</button>`;
    const statsRow = (statLine) => `
        <div class="gr-card-stats">
          <button class="gr-more-toggle" title="Details">More Info &#x25BE;</button>
          ${statLine ? `<span>${esc(statLine)}</span>` : ''}
        </div>`;
    // Tile art with emoji fallback (story tiles are server-verified to
    // exist; game tiles fall back via onerror until the art lands)
    const artCell = (tileUrl, emoji) => tileUrl
        ? `<div class="gr-card-icon gr-card-art"><img src="${esc(tileUrl)}" alt="" onerror="this.parentNode.classList.remove('gr-card-art');this.parentNode.textContent='${emoji}'"></div>`
        : `<div class="gr-card-icon">${emoji}</div>`;
    const moreHtml = (facts, rows) => `
        <div class="gr-card-more" style="display:none">
          ${rows.filter(r => r[1]).map(([k, v]) =>
              `<div class="gr-more-row"><span>${esc(k)}</span>${esc(v)}</div>`).join('')}
          ${(facts || []).length ? `<ul class="gr-more-facts">${facts.map(f => `<li>${esc(f)}</li>`).join('')}</ul>` : ''}
        </div>`;

    const storyCards = shownStories.map(s => {
        const st = s.stats || {};
        const statLine = [
            st.rooms ? `${st.rooms} rooms` : null,
            st.images ? `${st.images} illustrations` : null,
            st.endings > 1 ? `${st.endings} endings` : null,
            ...(s.tags || []),
        ].filter(Boolean).join(' · ');
        return `
        <div class="gr-card gr-card-story" data-story="${esc(s.slug)}">
          ${artCell(s.tile, '📖')}
          <div class="gr-card-body">
            <div class="gr-card-title">${esc(s.title || s.slug)}
              <span class="gr-card-genre">story</span></div>
            <div class="gr-card-desc">${esc(s.description || '')}</div>
            ${statsRow(statLine)}
          </div>
          <div class="gr-card-side">
            <div class="gr-card-btns">
              ${playCtl('New PRIVATE playthrough — sealed and hidden whenever the vault locks')}
              <button class="pk-btn gr-card-story-gear" data-story="${esc(s.slug)}" title="${esc(s.title || s.slug)} — GM settings">&#x2699;&#xFE0E;</button>
            </div>
            <span class="gr-card-count">${counts['story:' + s.slug] ? counts['story:' + s.slug] + ' playthrough' + (counts['story:' + s.slug] > 1 ? 's' : '') : 'new tale'}</span>
          </div>
          ${moreHtml(s.facts, [
              ['her role', s.role ? `${s.role} (or yourself — three identity modes)` : ''],
              ['you play', s.player_role || ''],
              ['art', st.images ? `${st.images} scenes, ${artFmt(st.art_kb || 0)}` : ''],
              ['tags', (s.tags || []).join(', ')],
          ])}
        </div>`;
    }).join('');

    const gameCards = shown.map(g => {
        const statLine = [g.genre || null, g.players || null].filter(Boolean).join(' · ');
        return `
        <div class="gr-card" data-game="${esc(g.id)}">
          ${artCell(g.tile ? `/plugin-web/${g.plugin_name}/${g.tile}` : null, esc(g.icon || '\u{1F3B2}'))}
          <div class="gr-card-body">
            <div class="gr-card-title">${esc(g.title || g.id)}
              ${g.genre ? `<span class="gr-card-genre">${esc(g.genre)}</span>` : ''}</div>
            <div class="gr-card-desc">${esc(g.desc || '')}</div>
            ${statsRow(statLine)}
          </div>
          <div class="gr-card-side">
            <div class="gr-card-btns">
              ${playCtl('New PRIVATE session — sealed and hidden whenever the vault locks')}
              <button class="pk-btn gr-card-gear" data-game="${esc(g.id)}" title="${esc(g.title || g.id)} settings">&#x2699;&#xFE0E;</button>
            </div>
            <span class="gr-card-count">${counts[g.id] ? counts[g.id] + ' session' + (counts[g.id] > 1 ? 's' : '') : 'new table'}</span>
          </div>
          ${moreHtml(g.facts, [
              ['players', g.players || ''],
              ['from plugin', g.plugin_name || ''],
          ])}
        </div>`;
    }).join('');

    shelf.innerHTML = (gameCards + storyCards)
        || `<div class="gr-empty">${(_roomGames.length + _stories.length) ? 'Nothing matches.' : 'No games installed. Enable a plugin that brings one (poker lives in this plugin — is it signed?).'}</div>`;

    shelf.querySelectorAll('.gr-card-gear').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();   // the card click underneath opens the game
            const mod = await import(`./settings-modal.js?v=${bootV()}`);
            mod.openGameSettings(btn.dataset.game);
        });
    });
    shelf.querySelectorAll('.gr-card-story-gear').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const mod = await import(`./settings-modal.js?v=${bootV()}`);
            mod.openStorySettings(btn.dataset.story);
        });
    });
    // ▾ on the split Play button: toggle this card's menu, close any other
    shelf.querySelectorAll('.gr-play-caret').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();   // the card click underneath opens the game
            const menu = btn.parentNode.querySelector('.gr-play-menu');
            if (!menu) return;
            const wasOpen = menu.style.display !== 'none';
            shelf.querySelectorAll('.gr-play-menu').forEach(m => { m.style.display = 'none'; });
            if (!wasOpen) menu.style.display = '';
        });
    });
    shelf.querySelectorAll('.gr-play-priv').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();   // the card click underneath opens PUBLIC
            const card = btn.closest('.gr-card');
            const menu = btn.closest('.gr-play-menu');
            if (menu) menu.style.display = 'none';
            btn.disabled = true;
            try {
                if (card?.dataset.story) {
                    const s = _stories.find(x => x.slug === card.dataset.story);
                    if (s) await openStory(s, { priv: true });
                    return;
                }
                const g = _roomGames.find(x => x.id === card?.dataset.game);
                if (!g) return;
                const session = await room.createPrivateSession(g.id, g.id);
                await room.openRoom(_root, g, session, { back: renderLibrary, games: _roomGames });
            } catch (e2) {
                console.error('[GameRoom] private session failed', e2);
                ui.showToast(e2.message, 'error');
                btn.disabled = false;
            }
        });
    });
    shelf.querySelectorAll('.gr-more-toggle').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();   // the card click underneath opens the game
            const more = btn.closest('.gr-card')?.querySelector('.gr-card-more');
            if (!more) return;
            const open = more.style.display !== 'none';
            more.style.display = open ? 'none' : '';
            btn.innerHTML = open ? 'More Info &#x25BE;' : 'More Info &#x25B4;';
        });
    });
    shelf.querySelectorAll('.gr-card').forEach(el => {
        el.addEventListener('click', () => {
            if (el.dataset.story) {
                const s = _stories.find(x => x.slug === el.dataset.story);
                if (s) openStory(s);
                return;
            }
            const g = _roomGames.find(x => x.id === el.dataset.game);
            if (!g) return;
            room.openRoom(_root, g, null, { back: renderLibrary, games: _roomGames })
                .catch(e => {
                    console.error('[GameRoom] failed to open', g.id, e);
                    if (_root) _root.innerHTML = `<div class="gr-lib"><div class="gr-empty">Couldn't open ${room.esc(g.title || g.id)}: ${room.esc(e.message)}</div></div>`;
                });
        });
    });
}

// Story tile → playthrough chat (mode-tagged story:<slug>) → the story room:
// the real chat rail transplanted into a story frame (Plan B,
// tmp/story-primitive-plan.md). The 📖 chat accordion stays the escape hatch.
async function openStory(st, opts) {
    try {
        // priv: a NEW playthrough born private (Play Private) — resume
        // stays on ensureSession, which naturally finds private ones too
        // while the vault is open (they're just visible chats then).
        const session = opts?.priv
            ? await room.createPrivateSession('story:' + st.slug, st.slug)
            : await room.ensureSession('story:' + st.slug, st.slug);
        if (!storyRoom) storyRoom = await import(`./story-room.js?v=${bootV()}`);
        await storyRoom.openStoryRoom(_root, st, session, { back: renderLibrary, stories: _stories });
    } catch (e) {
        console.error('[GameRoom] story open failed', st.slug, e);
        ui.showToast(e.message, 'error');
    }
}
