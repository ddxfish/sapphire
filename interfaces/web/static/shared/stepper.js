// shared/stepper.js - a wizard's bones (tmp/setup-wizard-plan.md, 2026-10-22).
//
// Steps in a row, pips above, Back / Next / Finish below, and the gate between
// the required lane and the optional one. The setup wizard (views/setup.js) is
// the first stepper; a Persona Builder would be the second.
//
//   const st = createStepper({ el, steps, onShow, onFinish, onExit });
//   st.go('voice');   st.next();   st.back();   st.repaint();   st.destroy();
//
// A step: { id, title, render(body), leave?(body, dir), unmount?(body),
//           kind?: 'gate', optional?: true }
//   render(body)       paint into the fresh body element (may be async)
//   leave(body, dir)   before moving on (+1) or back (-1): save; return false to stay
//   unmount(body)      stop what render started (the host's unmount for a hosted tab)
//   kind 'gate'        the required lane is done: Finish, or carry on to the optional steps
//   optional           after the gate: Finish is always offered beside Next
import { escapeHtml as esc } from './modal.js';

export function createStepper({ el, steps, onShow, onFinish, onExit, exitLabel = 'Skip for now' }) {
    let i = -1;
    let busy = false;
    let body = null;

    el.classList.add('stepper');
    el.innerHTML = `
        <div class="stepper-head">
            <div class="stepper-pips"></div>
            <div class="stepper-count"></div>
            <h2 class="stepper-title"></h2>
        </div>
        <div class="stepper-body"></div>
        <div class="stepper-foot"></div>`;
    const pipsEl = el.querySelector('.stepper-pips');
    const countEl = el.querySelector('.stepper-count');
    const titleEl = el.querySelector('.stepper-title');
    const bodyHost = el.querySelector('.stepper-body');
    const footEl = el.querySelector('.stepper-foot');

    pipsEl.addEventListener('click', e => {
        const pip = e.target.closest('.stepper-pip');
        if (pip && pip.classList.contains('done')) go(pip.dataset.id);   // back to a step already passed
    });
    footEl.addEventListener('click', e => {
        const b = e.target.closest('button[data-act]');
        if (!b || busy) return;
        ({ back, next, finish, exit })[b.dataset.act]?.();
    });

    function paintChrome() {
        const step = steps[i];
        pipsEl.innerHTML = steps.map((s, n) =>
            `<span class="stepper-pip${n < i ? ' done' : n === i ? ' current' : ''}${s.optional ? ' optional' : ''}" data-id="${esc(s.id)}" title="${esc(s.title)}"></span>`).join('');
        countEl.textContent = `${i + 1} of ${steps.length}`;
        titleEl.textContent = step.title;
        const last = i === steps.length - 1;
        const btn = (act, label, cls = '') => `<button class="btn ${cls}" data-act="${act}">${label}</button>`;
        let left = '', right = '';
        if (step.kind === 'gate') {
            left = i > 0 ? btn('back', '← Back') : '';
            right = btn('finish', 'Finish ✓', 'btn-primary') + (last ? '' : btn('next', 'More options →'));
        } else if (step.optional) {
            left = btn('back', '← Back');
            right = btn('finish', 'Finish ✓', last ? 'btn-primary' : '') + (last ? '' : btn('next', 'Next →', 'btn-primary'));
        } else {
            left = (onExit ? `<button class="btn-link" data-act="exit">${esc(exitLabel)}</button>` : '')
                 + (i > 0 ? btn('back', '← Back') : '');
            right = btn('next', 'Next →', 'btn-primary');
        }
        footEl.innerHTML = `<div class="stepper-foot-left">${left}</div><div class="stepper-foot-right">${right}</div>`;
        setBusy(busy);
    }

    function setBusy(b) {
        busy = !!b;
        footEl.querySelectorAll('button').forEach(x => { x.disabled = busy; });
        el.classList.toggle('busy', busy);
    }

    async function unmountCurrent() {
        if (i < 0 || !body) return;
        try { await steps[i].unmount?.(body); } catch (e) { console.warn('[stepper] unmount failed:', e); }
        body.remove();
        body = null;
    }

    async function show(n) {
        await unmountCurrent();
        i = n;
        body = document.createElement('div');
        body.className = 'stepper-step';
        body.dataset.step = steps[i].id;
        bodyHost.appendChild(body);
        paintChrome();
        el.scrollTop = 0;
        try { await steps[i].render(body); } catch (e) {
            console.warn('[stepper] render failed:', e);
            body.innerHTML = `<p class="setting-help">This step could not be shown: ${esc(e.message)}</p>`;
        }
        onShow?.(steps[i]);
    }

    async function move(dir, after) {
        if (busy) return;
        setBusy(true);
        try {
            if (i >= 0 && dir !== 0) {
                const ok = await steps[i].leave?.(body, dir);
                if (ok === false) return;
            }
            await after();
        } finally { setBusy(false); }
    }

    const go = id => { const n = steps.findIndex(s => s.id === id); return move(n > i ? 1 : -1, () => show(n < 0 ? 0 : n)); };
    const next = () => move(1, () => show(Math.min(i + 1, steps.length - 1)));
    const back = () => move(-1, () => show(Math.max(i - 1, 0)));
    const finish = () => move(1, () => onFinish?.(steps[i]));
    const exit = () => onExit?.(steps[i]);

    return {
        go, next, back, finish,
        current: () => steps[i] || null,
        body: () => body,
        // paint the current step again (after a save or a data reload); same scroll
        async repaint() { if (i < 0 || !body) return; await unmountCurrent(); await show(i); },
        setBusy,
        async destroy() { await unmountCurrent(); i = -1; el.innerHTML = ''; el.classList.remove('stepper'); },
    };
}
