// Corpus for shared/mic-words.js, run under node by tests/test_mic_words_js.py:
// which refusal gets which words, and the chosen mic never becomes a hard demand.
import assert from 'node:assert/strict';

const store = {};
const media = { getUserMedia() {} };
Object.defineProperty(globalThis, 'navigator', { value: { mediaDevices: media }, configurable: true });
globalThis.window = { isSecureContext: true };
globalThis.localStorage = {
    getItem: k => store[k] ?? null,
    setItem: (k, v) => { store[k] = v; },
    removeItem: k => { delete store[k]; },
};

const { micWords, micWhy, micAudio, chooseMic, chosenMic } = await import('../../interfaces/web/static/shared/mic-words.js');
const denied = { name: 'NotAllowedError', message: 'Permission denied' };

// a site block points at the address bar; an allowed site points at the computer
assert.match(micWords(denied, 'denied'), /left of the address/);
assert.match(micWords(denied), /left of the address/);
assert.match(micWords(denied, 'granted'), /privacy settings/);
assert.match(micWords({ name: 'NotAllowedError', message: 'Permission denied by system' }), /privacy settings/);
assert.match(micWords({ name: 'NotReadableError' }), /busy or blocked/);
assert.match(micWords({ name: 'NotFoundError' }), /No microphone found/);

// every one fits a toast title: shared/notify.js cuts at 200
for (const e of [denied, { name: 'NotReadableError' }, { name: 'NotFoundError' }, { name: 'X' }])
    for (const perm of [null, 'granted', 'denied'])
        assert.ok(micWords(e, perm).length <= 200, micWords(e, perm));

// micWhy asks the browser for the state itself, and survives one that won't say
navigator.permissions = { query: async () => ({ state: 'granted' }) };
assert.match(await micWhy(denied), /privacy settings/);
navigator.permissions = { query: async () => { throw new TypeError('microphone'); } };
assert.match(await micWhy(denied), /left of the address/);

// no secure page wins over everything
window.isSecureContext = false;
assert.match(micWords(denied, 'granted'), /secure page/);
window.isSecureContext = true;

// the chosen mic: absent = the caller's ask untouched; present = ideal, never exact
assert.deepEqual(micAudio({ channelCount: 1 }), { channelCount: 1 });
chooseMic('abc');
assert.equal(chosenMic(), 'abc');
assert.deepEqual(micAudio({ channelCount: 1 }), { channelCount: 1, deviceId: { ideal: 'abc' } });
chooseMic('');
assert.equal(chosenMic(), '');
assert.deepEqual(micAudio({}), {});

console.log('mic-words corpus passed');
