// Heads-Up Hold'em — game module. Board + actions only; the room host owns
// the chrome (stage bar, sidebar, the REAL chat rail + composer, her quip
// strip, voice, API plumbing). Table talk is the chat (F6 port, 2026-09-09);
// a move carries no talk line of its own. Helpers (esc, prettyCodes) arrive
// via ctx — no direct host import, so the cache-busted host stays one instance.
// Plan A (same day): the host attaches the composer's text to every move as
// `say`; the server lands move + words as the player's row on the chat.

const SUIT = { s: '♠', h: '♥', d: '♦', c: '♣' };
const RED = { h: true, d: true };

function cardHtml(code, down) {
    if (down) return '<div class="pk-card pk-down"></div>';
    if (!code) return '<div class="pk-card pk-slot"></div>';
    const r = code[0] === 'T' ? '10' : code[0];
    const s = SUIT[code[1]] || '?';
    const red = RED[code[1]] ? ' pk-red' : '';
    return `<div class="pk-card${red}">
        <span class="pk-corner">${r}<i>${s}</i></span>
        <span class="pk-pip">${s}</span>
        <span class="pk-corner pk-corner-b">${r}<i>${s}</i></span>
    </div>`;
}

function renderBoard(el, state, ctx) {
    const { esc, prettyCodes } = ctx;
    if (!state || !state.session) {
        ctx.stageInfo?.('');
        el.innerHTML = `
          <div class="pk-splash">
            <h2>♠ Heads-Up Hold'em</h2>
            <p>She talks every move, right here in the chat. Type a needle and click your move to send it with the play — or Send it on its own.</p>
          </div>`;
        return;
    }
    const sess = state.session;
    const hand = state.hand;
    const over = !hand || hand.street === 'over';
    ctx.stageInfo?.(hand ? `Hand #${hand.num} &middot; ${esc(hand.street)}` : 'between hands');
    const board = (hand && hand.board) || [];
    const boardSlots = [0, 1, 2, 3, 4].map(i => cardHtml(board[i] || null)).join('');
    const aiHole = hand ? (hand.ai_hole ? hand.ai_hole.map(c => cardHtml(c)).join('')
                                        : cardHtml(null, true) + cardHtml(null, true)) : '';
    const myHole = hand ? hand.player_hole.map(c => cardHtml(c)).join('') : '';
    const dealerChip = w => (hand && hand.dealer === w) ? '<span class="pk-dbtn" title="Dealer button">D</span>' : '';
    const betChip = w => (hand && !over && hand.street_bets[w] > 0)
        ? `<div class="pk-betchip">${hand.street_bets[w]}</div>` : '';
    const busted = sess.player_chips <= 0 || sess.ai_chips <= 0;

    el.innerHTML = `
      <div class="pk-stage">
        <div class="pk-felt">
          <div class="pk-seat pk-seat-ai ${ctx.busy() ? 'pk-thinking' : ''}">
            <div class="pk-avatar">\u{1FA75}</div>
            <div class="pk-seat-info">
              <div class="pk-name">${esc(sess.ai_name)} ${dealerChip('ai')}</div>
              <div class="pk-chips">${sess.ai_chips}</div>
            </div>
            <div class="pk-hole">${aiHole}</div>
            ${betChip('ai')}
          </div>
          <div class="pk-center">
            <div class="pk-board">${boardSlots}</div>
            ${hand ? `<div class="pk-pot">pot ${hand.pot}</div>` : ''}
          </div>
          <div class="pk-seat pk-seat-me">
            ${betChip('player')}
            <div class="pk-hole">${myHole}</div>
            <div class="pk-seat-info">
              <div class="pk-name">${esc(sess.player_name)} ${dealerChip('player')}</div>
              <div class="pk-chips">${sess.player_chips}</div>
            </div>
          </div>
        </div>
        ${over && hand && hand.result ? `<div class="pk-result">${prettyCodes(esc(hand.result.desc))}</div>` : ''}
        ${busted ? `<div class="pk-result pk-final">${sess.player_chips <= 0 ? esc(sess.ai_name) + ' has the table. Rematch?' : 'You felted ' + esc(sess.ai_name) + '. Champion.'}</div>` : ''}
      </div>`;
}

function renderActions(el, state, ctx) {
    if (!state || !state.session) {
        el.innerHTML = '<button class="pk-btn pk-btn-primary" id="pk-sit">Sit down</button>';
        el.querySelector('#pk-sit').onclick = () => ctx.post('new-session', {}, 'shuffling');
        return;
    }
    const sess = state.session;
    const hand = state.hand;
    const over = !hand || hand.street === 'over';
    const busted = sess.player_chips <= 0 || sess.ai_chips <= 0;
    if (busted) {
        el.innerHTML = '<button class="pk-btn pk-btn-primary" id="pk-again">New session</button>';
        el.querySelector('#pk-again').onclick = () => ctx.post('new-session', {}, 'shuffling');
        return;
    }
    if (over) {
        el.innerHTML = '<button class="pk-btn pk-btn-primary" id="pk-deal">Deal next hand</button>';
        el.querySelector('#pk-deal').onclick = () => ctx.post('start', {}, 'dealing');
        return;
    }
    if (hand.to_act !== 'player') {
        el.innerHTML = '<div class="pk-wait">Waiting&hellip;</div>';
        return;
    }

    const legal = hand.legal || { actions: [] };
    const canSize = legal.actions.includes('bet') || legal.actions.includes('raise');
    const sizeVerb = legal.actions.includes('bet') ? 'bet' : 'raise';
    let html = '';
    if (legal.actions.includes('fold'))
        html += '<button class="pk-btn pk-act" data-a="fold">Fold</button>';
    if (legal.actions.includes('check'))
        html += '<button class="pk-btn pk-act" data-a="check">Check</button>';
    if (legal.actions.includes('call'))
        html += `<button class="pk-btn pk-act pk-btn-primary" data-a="call">Call ${legal.to_call}</button>`;
    if (canSize) {
        const min = legal.min_raise_to, max = legal.max_raise_to;
        const toCall = legal.to_call || 0;
        const myBet = hand.street_bets.player;
        const base = hand.pot + toCall;
        const clamp = v => Math.max(min, Math.min(max, Math.round(v / 5) * 5));
        const presets = [
            ['Min', min],
            ['½ pot', clamp(myBet + toCall + base / 2)],
            ['Pot', clamp(myBet + toCall + base)],
            ['All-in', max],
        ];
        html += `<span class="pk-sizer">
            <input type="number" class="pk-amt" id="pk-amt" min="${min}" max="${max}" step="5" value="${min}">
            <button class="pk-btn pk-act pk-btn-primary" data-a="${sizeVerb}">${sizeVerb === 'bet' ? 'Bet' : 'Raise to'}</button>
        </span>`;
        html += presets.map(p => `<button class="pk-btn pk-btn-sm pk-preset" data-v="${p[1]}">${p[0]}</button>`).join('');
    }
    el.innerHTML = html;
    const amt = el.querySelector('#pk-amt');
    el.querySelectorAll('.pk-preset').forEach(b => b.onclick = () => { if (amt) amt.value = b.dataset.v; });
    el.querySelectorAll('.pk-act').forEach(b => b.onclick = () => {
        // The move only — anything typed in the composer is a chat turn the
        // player sends themselves (the seat hears it through the mirror).
        const body = { action: b.dataset.a, args: {} };
        if ((b.dataset.a === 'bet' || b.dataset.a === 'raise') && amt) body.args.amount = parseInt(amt.value, 10);
        ctx.post('act', body, 'thinking');
    });
}

export const game = {
    id: 'poker',
    title: "Heads-Up Hold'em",
    icon: '♠',
    renderBoard,
    renderActions,
};
