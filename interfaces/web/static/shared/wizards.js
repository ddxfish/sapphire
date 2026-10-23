// shared/wizards.js - the wizards this Sapphire has (tmp/setup-wizard-plan.md, 2026-10-22).
//
// A registry: { id, title, blurb, open() }. The setup wizard registers itself;
// the "More wizards" step at the end of setup lists the others. A plugin or a
// later core wizard (the Persona Builder) registers the same way.
const _wizards = new Map();

export function register(w) {
    if (!w?.id || typeof w.open !== 'function') return;
    _wizards.set(w.id, { id: w.id, title: w.title || w.id, blurb: w.blurb || '', open: w.open });
}

export function unregister(id) { _wizards.delete(id); }

/** Every wizard, in registration order; `except` leaves one out (the one asking). */
export function list(except = null) {
    return [..._wizards.values()].filter(w => w.id !== except);
}
