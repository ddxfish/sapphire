// shared/mic-words.js - why the browser wouldn't give us the mic, in words, and
// which mic to ask for. One voice for every getUserMedia caller (push-to-talk,
// Conversation mode, the browser mic test).

// No mediaDevices at all = not a secure page (plain http from another device):
// the browser hides the mic API entirely, it isn't a denial.
export const micAvailable = () => !!navigator.mediaDevices?.getUserMedia;

// The long form, for the test panel. micWords stays under a toast title's 200
// characters (shared/notify.js cuts the rest).
export const MIC_PRIVACY_STEPS = "On Windows: Settings > Privacy & security > Microphone, turn on microphone access and " +
    "access for desktop apps. On a Mac: System Settings > Privacy & Security > Microphone.";

// perm: this site's permission state, when known. A refusal with the site
// already allowed came from somewhere else, so the address bar can't fix it.
export function micWords(e, perm = null) {
    if (!micAvailable() || !window.isSecureContext)
        return "This browser only shares the mic on a secure page. Open Sapphire with https://, " +
               "or on the computer it runs on (localhost).";
    const n = e?.name || '';
    if (n === 'NotAllowedError' || n === 'SecurityError') {
        if (perm === 'granted' || /system/i.test(e?.message || ''))
            return "This site is allowed, but the computer is blocking the browser's mic. Check its microphone privacy settings.";
        return 'The browser blocked the mic. Click the icon left of the address, allow the microphone, then try again.';
    }
    if (n === 'NotFoundError' || n === 'OverconstrainedError' || e?.message?.includes('not be found'))
        return 'No microphone found. Plug one in, or pick one in your computer\'s sound settings.';
    if (n === 'NotReadableError' || n === 'AbortError')
        return 'The mic is busy or blocked by the computer. Close other apps using it, check its microphone privacy settings, then try again.';
    return "Couldn't open the mic. Try again" + (e?.message ? ` (${e.message}).` : '.');
}

/** This site's mic permission: granted, denied, prompt, or null where the browser won't say. */
export async function micPermission() {
    try { return (await navigator.permissions.query({ name: 'microphone' })).state; } catch { return null; }
}

export const micWhy = async (e) => micWords(e, await micPermission());

// The mic picked in the browser mic test, kept per browser. `ideal`, never
// `exact`: an unplugged mic falls back to the default instead of failing.
const CHOSEN_KEY = 'sapphire-browser-mic';

export function chosenMic() {
    try { return localStorage.getItem(CHOSEN_KEY) || ''; } catch { return ''; }
}

export function chooseMic(id) {
    try { id ? localStorage.setItem(CHOSEN_KEY, id) : localStorage.removeItem(CHOSEN_KEY); } catch {}
}

export function micAudio(wanted) {
    const id = chosenMic();
    return id ? { ...wanted, deviceId: { ideal: id } } : wanted;
}
