// surface/organs.js — the organ transplant (tmp/story-primitive-plan.md).
//
// The send stack is a singleton: ui.js and core/state.js cache element refs
// at boot, core/events.js binds to elements or document — never to ancestry.
// So a mode that wants the REAL rail (streaming, mic, TTS, stop, images,
// agent pills) doesn't clone or rebuild it: it re-parents the live nodes into
// its own frame and returns them on exit. Moved nodes keep their listeners
// and every module ref stays valid — same organs, just visiting.
//
// Organs:
//   railBundle     — the single node #chatbg (overlay + #chat-container inside)
//   composerBundle — ALL current children of chat's .form-wrapper (context
//                    bar, image preview, agent pill bar if present, #chat-form,
//                    prompt/functions displays). All-children, not a fixed id
//                    list: agent-status.js inserts its pill bar dynamically.
//
// Single holder. A new claim auto-releases the previous one. Release restores
// in reverse claim order so each node's recorded nextSibling is already home.
// Safe even if the borrowing frame's DOM was innerHTML-wiped — the nodes get
// detached, not destroyed, and we hold direct refs.

import { getIsProc } from '../core/state.js';

let _holder = null;
let _records = [];   // {node, parent, next} in claim order

function restoreAll() {
    for (let i = _records.length - 1; i >= 0; i--) {
        const { node, parent, next } = _records[i];
        try {
            parent.insertBefore(node, (next && next.parentNode === parent) ? next : null);
        } catch (e) {
            console.error('[Organs] restore failed', e);
        }
    }
    _records = [];
}

// Move the organs into a frame. Refuses (returns false) while a turn is
// processing — same rule handleChatChange enforces — or if the anatomy is
// missing. On success the caller owns them until releaseOrgans(ownerId).
export function claimOrgans({ railSlot, composerSlot }, ownerId) {
    if (getIsProc()) return false;
    if (_holder) restoreAll();               // auto-release previous holder
    _holder = null;

    const rail = document.getElementById('chatbg');
    const formWrap = document.querySelector('#view-chat .form-wrapper');
    if (!rail || !formWrap || !railSlot || !composerSlot) return false;

    _records.push({ node: rail, parent: rail.parentNode, next: rail.nextSibling });
    railSlot.appendChild(rail);
    for (const child of [...formWrap.children]) {
        _records.push({ node: child, parent: formWrap, next: child.nextSibling });
        composerSlot.appendChild(child);
    }
    _holder = ownerId || 'unknown';
    return true;
}

// Return every organ to its recorded home. Idempotent. With an ownerId, only
// that holder's claim is released (a stale closer can't evict a new claim).
export function releaseOrgans(ownerId) {
    if (!_holder) return;
    if (ownerId && ownerId !== _holder) return;
    restoreAll();
    _holder = null;
}

export const organHolder = () => _holder;
