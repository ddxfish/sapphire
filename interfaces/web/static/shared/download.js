// shared/download.js — THE way the app saves a file to the user's disk.
// Two Chromium traps, both "Failed - Network error" in the downloads list
// (Brave, 2026-10-26):
//  - a bare <a href download> to our URL: on the self-signed cert the page's
//    connection is trusted, the browser's download fetch is not. So a URL is
//    fetched HERE, on the page's connection, then saved as a blob.
//  - a blob URL revoked in the same tick as click(): the browser reads it
//    after click() returns. Revoked late.
import { showToast } from './notify.js';
import { responseError, errWords } from './fetch.js';

export function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
}

export function saveJson(name, data) {
    saveBlob(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }), name);
}

// A same-origin URL (session cookie rides along). Says why on failure.
export async function saveUrl(url, name) {
    try {
        const res = await fetch(url, { credentials: 'same-origin' });
        if (!res.ok) throw await responseError(res);
        saveBlob(await res.blob(), name);
    } catch (e) {
        showToast(`Couldn't download ${name}. ${errWords(e)}`, 'error');
    }
}
