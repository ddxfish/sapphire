// shared/vault-api.js — prompt-vault lifecycle calls.
// Wrong key comes back as 403 (NOT 401 — fetch.js would bounce to /login).
// The lock POST is synchronous server-side: when it resolves, the world IS
// locked — safe to follow with PUT private_chat:false (the 0b contract).
import { fetchWithTimeout } from './fetch.js';

const post = (url, body) => fetchWithTimeout(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {})
});

export const vaultSetup = (key) => post('/api/vault/setup', { key });
export const vaultUnlock = (key) => post('/api/vault/unlock', { key });
export const vaultLock = () => post('/api/vault/lock');

// Authoritative state off the standing /api/status channel.
export async function vaultStatus() {
    try {
        const s = await fetchWithTimeout('/api/status');
        return s?.vault || { exists: false, unlocked: false };
    } catch {
        return { exists: false, unlocked: false };
    }
}
