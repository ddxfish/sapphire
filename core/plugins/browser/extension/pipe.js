// pipe.js - the hidden page that holds the socket to Sapphire.
//
// Why a page and not the worker: Chromium lets an extension PAGE reach a
// Sapphire whose certificate you accepted once in a tab; the worker cannot.
// A page also lives on while the worker sleeps. It has no chrome.storage, so
// the worker hands it the address and the key (open), and every frame crosses
// between the two over a runtime port.
//
// The key never leaves this browser: each connection carries a one-time proof
// made with it (HMAC-SHA256), offered as a WebSocket subprotocol.
let cfg = null;        // {url, keyId, key, skew}
let ws = null;
let port = null;
let timer = null;
let wait = 5000;       // Sapphire counts attempts: never faster than this
let corrected = false; // the clock was corrected once for this run of refusals
let state = { state: 'off', detail: '' };

const enc = (s) => new TextEncoder().encode(s);
const hex = (b) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, '0')).join('');

function worker() {
  if (!port) {
    port = chrome.runtime.connect({ name: 'pipe' });
    port.onMessage.addListener(fromWorker);
    port.onDisconnect.addListener(() => { port = null; });
  }
  return port;
}
const post = (m) => { try { worker().postMessage(m); } catch (e) { port = null; } };

function say(s, detail) {
  state = { state: s, detail: detail || '' };
  post({ pipe: 'state', ...state });
}

function fromWorker(m) {
  if (m.open) {
    const same = cfg && cfg.url === m.open.url && cfg.keyId === m.open.keyId && cfg.key === m.open.key;
    if (same && (ws || timer) && !m.open.now) return post({ pipe: 'state', ...state });
    cfg = m.open;
    wait = 5000; corrected = false;
    hangUp();
    dial();
  } else if (m.shut) {
    cfg = null;
    hangUp();
    say('off');
  } else if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify(m));
  }
}

function hangUp() {
  clearTimeout(timer); timer = null;
  if (ws) { const old = ws; ws = null; old.onclose = null; try { old.close(); } catch (e) {} post({ pipe: 'closed' }); }
}

function plan(ms) { clearTimeout(timer); timer = setTimeout(() => { timer = null; dial(); }, ms); }

async function proof(c) {
  const at = Math.floor(Date.now() / 1000) + (c.skew || 0);
  const nonce = hex(crypto.getRandomValues(new Uint8Array(12)));
  const key = await crypto.subtle.importKey('raw', enc(c.key), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const sig = hex(await crypto.subtle.sign('HMAC', key, enc(`browser/pipe\n${c.keyId}\n${at}\n${nonce}`)));
  return ['sapphire-device', `sapphire.v1.${c.keyId}.${at}.${nonce}.${sig}`];
}

async function dial() {
  if (!cfg || ws) return;
  const c = cfg;
  say('connecting');
  let s;
  try {
    s = new WebSocket(c.url, await proof(c));
  } catch (e) {
    say('unreachable', String(e && e.message || e));
    return plan(60000);
  }
  if (cfg !== c) { try { s.close(); } catch (e) {} return; }
  ws = s;
  let welcomed = false;
  s.onmessage = (e) => {
    let m; try { m = JSON.parse(e.data); } catch (err) { return; }
    if (!welcomed) { welcomed = true; wait = 5000; corrected = false; say('online'); }
    post(m);
  };
  s.onclose = (e) => {
    if (ws !== s) return;
    ws = null;
    post({ pipe: 'closed' });
    if (!cfg) return say('off');
    if (e.code === 4401) {                       // Sapphire did not accept the proof; her reason carries her clock
      const m = /^t=(\d+)$/.exec(e.reason || '');
      const skew = m ? Number(m[1]) - Math.floor(Date.now() / 1000) : 0;
      if (m && Math.abs(skew - (cfg.skew || 0)) > 60 && !corrected) {
        corrected = true; cfg.skew = skew;
        post({ pipe: 'skew', skew });
        return plan(5000);
      }
      say('refused');
      return plan(60000);
    }
    say(welcomed ? 'lost' : 'unreachable');
    plan(wait);
    wait = Math.min(wait * 2, 60000);
  };
}

chrome.runtime.onMessage.addListener((m) => { if (m && m.wakePipe) post({ pipe: 'state', ...state }); });
post({ pipe: 'ready' });
