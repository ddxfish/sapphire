// Webcam capture — listens for capture_webcam tool_start events
// and captures a frame from the user's camera.

import { fetchWithTimeout } from '/static/shared/fetch.js';

let capturing = false;

function showCapturePrompt() {
    return new Promise((resolve) => {
        // Remove any existing prompt
        document.getElementById('webcam-prompt')?.remove();

        const overlay = document.createElement('div');
        overlay.id = 'webcam-prompt';
        overlay.innerHTML = `
            <div style="position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.4);z-index:9998;display:flex;align-items:center;justify-content:center">
                <div style="background:var(--bg-secondary,#1e1e2e);border:1px solid var(--border-color,#444);border-radius:12px;padding:24px 32px;text-align:center;max-width:320px;box-shadow:0 8px 32px rgba(0,0,0,0.5)">
                    <div style="font-size:2em;margin-bottom:12px">📷</div>
                    <div style="color:var(--text-primary,#cdd6f4);font-size:1.1em;margin-bottom:16px">Sapphire wants to see you</div>
                    <button id="webcam-allow" style="background:var(--accent-color,#89b4fa);color:#1e1e2e;border:none;border-radius:8px;padding:10px 24px;font-size:1em;cursor:pointer;margin-right:8px;font-weight:600">Allow Camera</button>
                    <button id="webcam-deny" style="background:transparent;color:var(--text-secondary,#a6adc8);border:1px solid var(--border-color,#444);border-radius:8px;padding:10px 24px;font-size:1em;cursor:pointer">Deny</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);

        const cleanup = (result) => { overlay.remove(); resolve(result); };

        overlay.querySelector('#webcam-allow').addEventListener('click', () => cleanup(true));
        overlay.querySelector('#webcam-deny').addEventListener('click', () => cleanup(false));

        // Auto-dismiss after 12s (backend times out at 15s, leave margin)
        setTimeout(() => cleanup(false), 12000);
    });
}

async function doCapture(nonce) {
    // Request camera access — called from click handler so browser allows it
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } }
        });
    } catch (err) {
        const msg = `[Webcam] Camera access denied: ${err.message}. ` +
            (err.name === 'NotAllowedError'
                ? 'Grant camera permission in browser settings.'
                : 'Check that a camera is connected.');
        console.error(msg);
        await fetchWithTimeout('/api/plugin/webcam/capture', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ nonce, error: msg })
        });
        return;
    }

    // Capture a single frame
    const video = document.createElement('video');
    video.srcObject = stream;
    video.playsInline = true;
    await video.play();

    // Brief delay for camera to stabilize (auto-exposure/focus)
    await new Promise(r => setTimeout(r, 500));

    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);

    // Stop the camera immediately
    stream.getTracks().forEach(t => t.stop());

    // Convert to base64 JPEG
    const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
    const base64 = dataUrl.split(',')[1];

    // Deliver to backend with nonce
    const result = await fetchWithTimeout('/api/plugin/webcam/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nonce, data: base64, media_type: 'image/jpeg' })
    });

    if (result?.status === 'ok') {
        console.log('[Webcam] Capture delivered to backend');
    } else {
        console.warn('[Webcam] Backend rejected capture:', result?.error);
    }
}

async function onToolStart(e) {
    const { name } = e.detail;
    if (name !== 'capture_webcam' || capturing) return;

    capturing = true;
    console.log('[Webcam] Tool started — requesting user permission');

    try {
        // 1. Get the nonce from the pending capture request
        const pending = await fetchWithTimeout('/api/plugin/webcam/pending');
        if (!pending?.pending || !pending.nonce) {
            console.warn('[Webcam] No pending capture request');
            return;
        }

        // 2. Check secure context
        if (!window.isSecureContext) {
            const msg = `[Webcam] Camera blocked — not a secure context. ` +
                `Use https:// or access via localhost instead of ${location.hostname}`;
            console.error(msg);
            await fetchWithTimeout('/api/plugin/webcam/capture', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ nonce: pending.nonce, error: msg })
            });
            return;
        }

        // 3. Show prompt — getUserMedia must be called from a user click
        const allowed = await showCapturePrompt();
        if (!allowed) {
            console.log('[Webcam] User denied capture');
            await fetchWithTimeout('/api/plugin/webcam/capture', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ nonce: pending.nonce, error: 'User denied camera access' })
            });
            return;
        }

        // 4. Capture (inside click context)
        await doCapture(pending.nonce);

    } catch (err) {
        console.error('[Webcam] Capture failed:', err);
    } finally {
        capturing = false;
    }
}

export default {
    init() {
        document.addEventListener('sapphire:tool_start', onToolStart);
        console.log('[Webcam] Plugin script loaded');
    }
};
