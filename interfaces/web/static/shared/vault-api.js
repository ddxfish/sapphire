// shared/vault-api.js — prompt-vault lifecycle calls.
// Wrong key comes back as 403 (NOT 401 — fetch.js would bounce to /login).
// The lock POST is synchronous server-side: when it resolves, the world IS
// locked — private chats hidden, a private active chat already evicted.
// (The old 0b "lock then PUT private_chat:false" sequence is DEAD as of
// vaulted chats 2026-08-14 — a sealed chat's flag can't be flipped.)
import { fetchWithTimeout } from './fetch.js';

const post = (url, body) => fetchWithTimeout(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {})
});

export const vaultSetup = (key) => post('/api/vault/setup', { key });
export const vaultUnlock = (key) => post('/api/vault/unlock', { key });
export const vaultLock = () => post('/api/vault/lock');
// Change passphrase — current required even while unlocked; lock state preserved.
export const vaultRekey = (current, newKey) => post('/api/vault/rekey', { current, new: newKey });
// {kind:'prompt', name, direction:'in'|'out'} or {kind:'piece', comp_type, key, direction}
export const vaultMove = (payload) => post('/api/vault/move', payload);
// {direction, items:[{kind:'piece',comp_type,key} | {kind:'prompt',name}]} →
// {moved, failed, results:[{item, ok, msg}]}. One request, one SSE event.
// A big batch rewrites the vault per item on the server — give it minutes.
export const vaultMoveBatch = (payload) => fetchWithTimeout('/api/vault/move-batch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
}, 10 * 60 * 1000);

// Authoritative state off the standing /api/status channel.
// null = server UNREACHABLE — never conflate that with "no vault exists"
// (a dead server once made the eyeball offer vault SETUP; the backend's
// 409 held, but the dialog itself was a lie — Krem, flow 3, 2026-08-16).
export async function vaultStatus() {
    try {
        const s = await fetchWithTimeout('/api/status');
        return s?.vault || { exists: false, unlocked: false };
    } catch {
        return null;
    }
}
