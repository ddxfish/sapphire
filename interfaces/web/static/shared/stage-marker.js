// shared/stage-marker.js — the ONE stage-card renderer (2026-10-25). The
// open_stage tool's result (functions/stage.py) carries one marker line:
//   <!--STAGE:{"app":"browser","label":"Browser","icon":"🌐"}-->
// It draws as a small card under her tool row: "🌐 Browser — Open beside the
// chat". The tab that streamed her turn also opens it at once (openLive);
// every other tab painting the chat (a reload, the phone) only shows the card,
// so nothing starts playing twice. The marker is UI-only (core strips the
// model's copy). Sibling of ask-marker.js / files-marker.js.

const STAGE_RE = /<!--STAGE:(\{.*?\})-->[ \t]*\n?/g;

export function parseStageMarker(text) {
    const cards = [];
    const out = String(text || '').replace(STAGE_RE, (_, json) => {
        try {
            const c = JSON.parse(json);
            const app = String(c.app || '').replace(/[^a-z0-9_-]/gi, '').slice(0, 64);
            if (app) cards.push({ app, label: String(c.label || app).slice(0, 40), icon: String(c.icon || '').slice(0, 8) });
        } catch { /* a torn marker: no card */ }
        return '';
    });
    return { cards, text: out };
}

async function open(app) {
    const [{ openStage }, { getCurrentView }] = await Promise.all([import('../surface/stage.js'), import('../core/router.js')]);
    if (getCurrentView() !== 'chat') return false;          // the stage lives on the Chat page
    return openStage(app);
}

export function buildStageCards(cards) {
    return cards.map(c => {
        const card = document.createElement('div');
        card.className = 'stage-card';
        const label = document.createElement('span');
        label.textContent = `${c.icon ? c.icon + ' ' : ''}${c.label}`;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = 'Open beside the chat';
        btn.addEventListener('click', () => open(c.app));
        card.append(label, btn);
        return card;
    });
}

// The tab that ran her turn, and only while someone is looking at it: a
// pocketed phone or a background tab gets the card, not a film starting.
// The stream strips markers from the live tool result, so the payloads come
// on tool_end's own `stage` field (api.js -> sapphire:stage_open).
export function openLive(cards) {
    if (!cards.length || document.visibilityState !== 'visible' || !document.hasFocus()) return;
    open(cards[cards.length - 1].app).catch(() => {});
}

document.addEventListener('sapphire:stage_open', (e) => {
    const list = Array.isArray(e.detail) ? e.detail : [];
    openLive(list.filter(c => c && typeof c.app === 'string')
                 .map(c => ({ app: c.app.replace(/[^a-z0-9_-]/gi, '').slice(0, 64), label: c.label, icon: c.icon })));
});
