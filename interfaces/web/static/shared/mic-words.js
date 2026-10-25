// shared/mic-words.js - why the browser wouldn't give us the mic, in words.
// One voice for every getUserMedia caller (push-to-talk, Conversation mode).

// No mediaDevices at all = not a secure page (plain http from another device):
// the browser hides the mic API entirely, it isn't a denial.
export const micAvailable = () => !!navigator.mediaDevices?.getUserMedia;

export function micWords(e) {
    if (!micAvailable() || !window.isSecureContext)
        return "This browser only shares the mic on a secure page. Open Sapphire with https://, " +
               "or on the computer it runs on (localhost).";
    const n = e?.name || '';
    if (n === 'NotAllowedError' || n === 'SecurityError')
        return 'The browser blocked the mic. Click the icon left of the address, allow the microphone, then try again.';
    if (n === 'NotFoundError' || n === 'OverconstrainedError' || e?.message?.includes('not be found'))
        return 'No microphone found. Plug one in, or pick one in your computer\'s sound settings.';
    if (n === 'NotReadableError' || n === 'AbortError')
        return 'The mic is busy or blocked by the computer. Close other apps using it, then try again.';
    return "Couldn't open the mic. Try again" + (e?.message ? ` (${e.message}).` : '.');
}
