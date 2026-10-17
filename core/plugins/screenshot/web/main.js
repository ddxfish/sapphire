// Screenshot — browser screen-share capture for get_screenshot(source="user").
// Listens for the get_screenshot tool starting; if the backend registered a
// pending browser capture, prompts the user to share their screen, grabs one
// frame via getDisplayMedia, and POSTs it back. Mirrors the webcam plugin,
// swapping getUserMedia → getDisplayMedia.

import { fetchWithTimeout } from '/static/shared/fetch.js';

let capturing = false;
const MAX_DIM = 1568;  // long-edge cap before encode — vision models downscale anyway

function showSharePrompt() {
    return new Promise((resolve) => {
        document.getElementById('screenshot-prompt')?.remove();
        const overlay = document.createElement('div');
        overlay.id = 'screenshot-prompt';
        overlay.innerHTML = `
            <div style="position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.4);z-index:9998;display:flex;align-items:center;justify-content:center">
                <div style="background:var(--bg-secondary,#1e1e2e);border:1px solid var(--border-color,#444);border-radius:12px;padding:24px 32px;text-align:center;max-width:340px;box-shadow:0 8px 32px rgba(0,0,0,0.5)">
                    <div style="font-size:2em;margin-bottom:12px">🖥️</div>
                    <div style="color:var(--text-primary,#cdd6f4);font-size:1.1em;margin-bottom:8px">Sapphire wants to see your screen</div>
                    <div style="color:var(--text-secondary,#a6adc8);font-size:0.85em;margin-bottom:16px">You'll choose which screen or window to share.</div>
                    <button id="screenshot-allow" style="background:var(--accent-color,#89b4fa);color:#1e1e2e;border:none;border-radius:8px;padding:10px 24px;font-size:1em;cursor:pointer;margin-right:8px;font-weight:600">Share screen</button>
                    <button id="screenshot-deny" style="background:transparent;color:var(--text-secondary,#a6adc8);border:1px solid var(--border-color,#444);border-radius:8px;padding:10px 24px;font-size:1em;cursor:pointer">Deny</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);
        const cleanup = (result) => { overlay.remove(); resolve(result); };
        overlay.querySelector('#screenshot-allow').addEventListener('click', () => cleanup(true));
        overlay.querySelector('#screenshot-deny').addEventListener('click', () => cleanup(false));
        // Auto-dismiss while waiting for the user to click Share (backend waits 30s).
        setTimeout(() => cleanup(false), 20000);
    });
}

function postBack(nonce, payload) {
    return fetchWithTimeout('/api/plugin/screenshot/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nonce, ...payload })
    });
}

async function doCapture(nonce) {
    // getDisplayMedia must run from the user click (Share button) for the
    // browser to allow it.
    let stream;
    try {
        stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    } catch (err) {
        const msg = err.name === 'NotAllowedError'
            ? 'The user dismissed the screen-share picker.'
            : `Screen share failed: ${err.message}`;
        console.error('[Screenshot]', msg);
        await postBack(nonce, { error: msg });
        return;
    }

    try {
        const video = document.createElement('video');
        video.srcObject = stream;
        video.playsInline = true;
        await video.play();
        await new Promise(r => setTimeout(r, 200));  // let the first frame arrive

        const vw = video.videoWidth, vh = video.videoHeight;
        const scale = Math.min(1, MAX_DIM / Math.max(vw, vh));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(vw * scale));
        canvas.height = Math.max(1, Math.round(vh * scale));
        canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);

        const base64 = canvas.toDataURL('image/png').split(',')[1];
        const result = await postBack(nonce, { data: base64, media_type: 'image/png' });
        if (result?.status === 'ok') console.log('[Screenshot] Frame delivered');
        else console.warn('[Screenshot] Backend rejected frame:', result?.error);
    } finally {
        stream.getTracks().forEach(t => t.stop());  // drop the share indicator promptly
    }
}

async function onToolStart(e) {
    const { name } = e.detail || {};
    if (name !== 'get_screenshot' || capturing) return;

    capturing = true;
    try {
        // Only source="user" registers a pending capture. source="local" captures
        // server-side and registers nothing → bail.
        const pending = await fetchWithTimeout('/api/plugin/screenshot/pending');
        if (!pending?.pending || !pending.nonce) return;

        if (!window.isSecureContext) {
            await postBack(pending.nonce, {
                error: `Screen share blocked — not a secure context (${location.hostname}). ` +
                       `Open Sapphire over https:// or via localhost.`
            });
            return;
        }

        const allowed = await showSharePrompt();
        if (!allowed) {
            await postBack(pending.nonce, { error: 'The user declined to share their screen.' });
            return;
        }

        await doCapture(pending.nonce);
    } catch (err) {
        console.error('[Screenshot] capture flow failed:', err);
    } finally {
        capturing = false;
    }
}

export default {
    init() {
        document.addEventListener('sapphire:tool_start', onToolStart);
        console.log('[Screenshot] Plugin script loaded');
    }
};
