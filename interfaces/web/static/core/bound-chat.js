// core/bound-chat.js — which chat this tab's rail is BOUND to (F1, 2026-09-08).
//
// null  = the classic Chat view: the rail shows the server's active chat and
//         every turn / history call rides the active pointer (unchanged).
// name  = a room has claimed the organs for its session: history is read BY
//         NAME, turns are sent BY NAME (/api/chat/stream {chat}), cancel is
//         scoped, and a pointer move from another tab, phone, or daemon is
//         NOT adopted into this rail. The server runs a by-name turn on the
//         active chat exactly as before, and pins an A1 override to the
//         named chat only when the pointer has moved elsewhere.
//
// Leaf module on purpose (no imports): api.js, surface/organs.js and main.js
// all read it, and any of them importing each other would cycle.

let _bound = null;

export const getBoundChat = () => _bound;
export const setBoundChat = (name) => { _bound = (name && String(name)) || null; };
export const clearBoundChat = () => { _bound = null; };
