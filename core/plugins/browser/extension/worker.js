// worker.js - Sapphire Browser Bridge: the debugger on the tabs you share <-> the pipe to Sapphire.
//
// This side is deliberately thin. Sapphire's browser plugin does the reading
// and the clicking; this worker only (1) decides which tabs she may touch,
// (2) puts chrome.debugger on a tab when she takes it and takes it off when
// she lets go, and (3) passes DevTools commands in and events out, refusing
// every command that is not on ALLOWED below.
//
// What she can reach is decided HERE, in your browser:
//   mode      off | shared (only tabs in the "Sapphire" tab group, and tabs she opened) | all
//   never     sites that are never shared, whatever the mode
//   local     a page on your own network only if you shared that tab by hand
//   private   windows: never
// The socket itself lives in the hidden page pipe.html (see pipe.js for why).

const GROUP = 'Sapphire';
const DOWNLOAD_MS = 5000;       // a download that starts this soon after she acted is hers, and is stopped
const HER_TABS = 12;            // tabs she may have open at once
const PIPE_PATH = '/ws/plugin/browser/pipe';

// The DevTools commands Playwright needs to read a page, click and type in
// it, navigate it and take its picture. Nothing that reads cookies or
// storage, touches other tabs, sets files on an upload field, or changes how
// your pages look. (Runtime.evaluate is here: reading a page IS running
// script in it. So she can see what that page's own script can see.)
// The Network domain is NOT here and its events are never forwarded: they
// carry cookies and the forms you post. Sapphire's relay works without it.
const ALLOWED = new Set([
  'DOM.describeNode', 'DOM.getBoxModel', 'DOM.getContentQuads', 'DOM.getFrameOwner', 'DOM.resolveNode',
  'DOM.scrollIntoViewIfNeeded',
  'Emulation.setFocusEmulationEnabled',
  'Input.dispatchKeyEvent', 'Input.dispatchMouseEvent', 'Input.insertText',
  'Log.enable',
  'Page.addScriptToEvaluateOnNewDocument', 'Page.removeScriptToEvaluateOnNewDocument', 'Page.captureScreenshot',
  'Page.createIsolatedWorld', 'Page.enable', 'Page.getFrameTree', 'Page.getLayoutMetrics',
  'Page.getNavigationHistory', 'Page.handleJavaScriptDialog', 'Page.navigate', 'Page.navigateToHistoryEntry',
  'Page.reload', 'Page.setLifecycleEventsEnabled', 'Page.stopLoading',
  'Runtime.callFunctionOn', 'Runtime.enable', 'Runtime.evaluate', 'Runtime.getProperties',
  'Runtime.releaseObject', 'Runtime.releaseObjectGroup', 'Runtime.runIfWaitingForDebugger',
  'Target.setAutoAttach', 'Target.detachFromTarget',
]);

const cfg = { server: '', keyId: '', key: '', mode: 'shared', never: [], skew: 0 };
const held = new Map();         // tab id -> target id: the debugger is on these
const hers = new Set();         // tabs she opened: hers to navigate over and to close
const localOk = new Set();      // local-network tabs you shared by hand
let pipe = null;                // the hidden page's port
let acted = 0;                  // when she last clicked, typed or navigated
let epoch = 0;                  // counts every "let go of everything": an attach that was under way then is undone
const attaching = new Map();    // tab id -> the attach under way, so two asks for one tab are one attach
let status = { state: 'off', detail: '' };
let paired = { name: '', label: '' };
self.refused = [];              // the last commands that were refused, for the popup

const hex = (b) => [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
const b64 = (b) => btoa(String.fromCharCode(...b)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const rand = (n) => crypto.getRandomValues(new Uint8Array(n));
const post = (m) => { if (pipe) { try { pipe.postMessage(m); } catch (e) { pipe = null; } } };

function mint() {
  cfg.keyId = hex(rand(8));
  cfg.key = b64(rand(32));
  return chrome.storage.local.set({ keyId: cfg.keyId, key: cfg.key });
}

const ready = (async () => {
  const s = await chrome.storage.local.get(['server', 'keyId', 'key', 'mode', 'never', 'skew']);
  for (const k of Object.keys(cfg)) if (s[k] !== undefined) cfg[k] = s[k];
  if (!cfg.keyId || !cfg.key) await mint();
  const t = await chrome.storage.session.get(['held', 'hers', 'localOk', 'paired', 'status']);
  for (const [id, target] of t.held || []) held.set(id, target);
  for (const id of t.hers || []) hers.add(id);
  for (const id of t.localOk || []) localOk.add(id);
  if (t.paired) paired = t.paired;
  if (t.status) status = t.status;
})();

function remember() {
  chrome.storage.session.set({ held: [...held], hers: [...hers], localOk: [...localOk], paired });
  chrome.action.setBadgeBackgroundColor({ color: '#2f6fd6' });
  chrome.action.setBadgeText({ text: held.size ? String(held.size) : '' });
}

// --- which tabs she may touch ---------------------------------------------------

const host = (h) => String(h || '').toLowerCase().replace(/\.+$/, '');      // "nas.lan." is nas.lan

function isLocal(h) {
  if (h === 'localhost' || !h.includes('.') ||
      /\.(localhost|local|lan|internal|intranet|private|corp|home|box|localdomain|home\.arpa)$/.test(h)) return true;
  const m = /^(\d+)\.(\d+)\.\d+\.\d+$/.exec(h);
  if (!m) return false;
  const a = Number(m[1]), b = Number(m[2]);
  return a === 10 || a === 127 || a === 0 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) ||
    (a === 169 && b === 254) || (a === 100 && b >= 64 && b <= 127);
}

function addressWhy(url, tabId) {
  if (!url || url === 'about:blank') return '';
  let u;
  try { u = new URL(url); } catch (e) { return 'not a web page'; }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return 'not a web page';
  const h = host(u.hostname);
  if (cfg.never.some((n) => h === n || h.endsWith('.' + n))) return 'a site you never share';
  if (isLocal(h) && !localOk.has(tabId)) return 'a page on your own network that you have not shared by hand';
  return '';
}

async function groups() {
  try { return new Set((await chrome.tabGroups.query({ title: GROUP })).map((g) => g.id)); } catch (e) { return new Set(); }
}

async function why(tab, ours, mode = cfg.mode) {
  if (mode === 'off') return 'sharing is off';
  if (tab.incognito) return 'a private window';
  const w = addressWhy(tab.url || tab.pendingUrl || '', tab.id);
  if (w) return w;
  if (mode === 'all' || hers.has(tab.id)) return '';
  return (ours || await groups()).has(tab.groupId) ? '' : 'not shared';
}

async function share(tab) {
  const mine = await chrome.tabGroups.query({ title: GROUP, windowId: tab.windowId });
  if (mine.length) return chrome.tabs.group({ tabIds: [tab.id], groupId: mine[0].id });
  const id = await chrome.tabs.group({ tabIds: [tab.id], createProperties: { windowId: tab.windowId } });
  await chrome.tabGroups.update(id, { title: GROUP, color: 'blue' });
}

// --- the debugger ---------------------------------------------------------------

async function attach(tabId, more) {
  const tab = await chrome.tabs.get(tabId);
  const no = await why(tab);
  if (no) throw new Error(`that tab is not hers to use (${no})`);
  if (!held.has(tabId)) {
    if (!attaching.has(tabId)) attaching.set(tabId, debug(tabId).finally(() => attaching.delete(tabId)));
    await attaching.get(tabId);
  }
  return { tab: tabId, target: held.get(tabId), url: tab.url || tab.pendingUrl || '', title: tab.title || '',
           local: localOk.has(tabId), hers: hers.has(tabId), ...more };
}

async function debug(tabId) {
  const was = epoch;
  try {
    await chrome.debugger.attach({ tabId }, '1.3');
  } catch (e) {
    const text = (e && e.message) || String(e);
    throw new Error(/already attached/i.test(text) ? 'DevTools is open on that tab; close it there first' : text);
  }
  const all = await chrome.debugger.getTargets();
  if (was !== epoch) {                                    // everything was let go while this was under way
    chrome.debugger.detach({ tabId }).catch(() => {});
    throw new Error('she was let go of that browser');
  }
  held.set(tabId, (all.find((x) => x.tabId === tabId && x.type === 'page') || {}).id || '');
  remember();
}

function drop(tabId, reason) {
  if (!held.delete(tabId)) return;
  chrome.debugger.detach({ tabId }).catch(() => {});
  post({ ev: 'detached', tab: tabId, reason });
  remember();
}

function release(reason) {
  epoch++;
  for (const id of [...held.keys()]) drop(id, reason || 'released');
}

const cmds = {
  async tabs() {
    const ours = await groups(), out = [];
    for (const t of await chrome.tabs.query({})) {
      if (!(await why(t, ours))) {
        out.push({ tab: t.id, url: t.url || t.pendingUrl || '', title: t.title || '', active: t.active,
                   held: held.has(t.id), local: localOk.has(t.id), hers: hers.has(t.id) });
      }
    }
    return out;
  },
  attach: (m) => attach(m.tab),
  // Asked before every read, picture and action: is this tab, where it is NOW, still hers to use?
  async ok(m) {
    let no = 'it was closed';
    try { no = await why(await chrome.tabs.get(m.tab)); } catch (e) {}
    if (no) drop(m.tab, no);
    return no;
  },
  async new() {
    if (cfg.mode === 'off') throw new Error('sharing is off in that browser');
    if (hers.size >= HER_TABS) throw new Error(`you already have ${HER_TABS} tabs open there; close one first`);
    let t;
    try {
      t = await chrome.tabs.create({ url: 'about:blank', active: false });
    } catch (e) {                                          // no window is open: one for her, behind yours
      const w = await chrome.windows.create({ url: 'about:blank', focused: false });
      t = w.tabs[0];
    }
    hers.add(t.id);
    try { await share(t); } catch (e) {}                  // no tab strip to group in: it is still hers
    return attach(t.id);
  },
  async close(m) {
    if (!hers.has(m.tab)) throw new Error("that tab is the user's own; you can close only the tabs you opened");
    await chrome.tabs.remove(m.tab);
    return {};
  },
  release() { release(); return {}; },
  cdp(m) {                                                // no await before sendCommand: her commands keep their order
    if (!held.has(m.tab)) throw new Error('that tab is not in her hand');
    if (!ALLOWED.has(m.method)) throw new Error(`'${m.method}' is not allowed by the Sapphire extension`);
    if (m.method === 'Page.navigate') {
      const no = addressWhy((m.params || {}).url, m.tab);
      if (no) throw new Error(`she may not go there (${no})`);
    }
    if (/^Input\.|^Page\.(navigate|reload)/.test(m.method)) acted = Date.now();
    return chrome.debugger.sendCommand(m.session ? { tabId: m.tab, sessionId: m.session } : { tabId: m.tab },
                                       m.method, m.params || {});
  },
};

async function onFrame(m) {
  await ready;
  if (m.pipe) return onPipe(m);
  if (m.cmd === 'welcome') {
    paired = { name: String(m.name || ''), label: String(m.label || '') };
    remember();
    return post({ ev: 'hello', v: chrome.runtime.getManifest().version, ua: navigator.userAgent, browser: brand(),
                  mode: cfg.mode, origin: origin() });
  }
  const fn = cmds[m.cmd];
  try {
    if (!fn) throw new Error('unknown command');
    const ok = await fn(m);
    if (m.id != null) post({ id: m.id, ok });
  } catch (e) {
    const err = (e && e.message) || String(e);
    if (m.cmd === 'cdp') { self.refused.push(`${m.method}: ${err}`); self.refused = self.refused.slice(-20); }
    if (m.id != null) post({ id: m.id, err });
  }
}

// A frame inside a page she may read can be a page she may not (a never-shared
// site, your router). A frame from another site is a target of its own: it is
// let go the moment it appears, and she is never told it was there. (A frame
// from the SAME site as the page shares the page's process and is read with it.)
function frameWhy(method, params, tabId) {
  const info = params && params.targetInfo;
  if (method !== 'Target.attachedToTarget' || !info || info.type !== 'iframe') return '';
  return addressWhy(info.url, tabId);
}

chrome.debugger.onEvent.addListener((src, method, params) => ready.then(() => {
  if (src.tabId == null || !held.has(src.tabId) || method.startsWith('Network.')) return;
  if (frameWhy(method, params, src.tabId)) {
    const who = src.sessionId ? { tabId: src.tabId, sessionId: src.sessionId } : { tabId: src.tabId };
    chrome.debugger.sendCommand(who, 'Target.detachFromTarget', { sessionId: params.sessionId }).catch(() => {});
    return;
  }
  post({ ev: 'cdp', tab: src.tabId, session: src.sessionId || null, method, params });
}));

chrome.debugger.onDetach.addListener(async (src, reason) => {
  await ready;
  if (src.tabId == null || !held.delete(src.tabId)) return;
  post({ ev: 'detached', tab: src.tabId, reason });
  remember();
  if (reason === 'canceled_by_user') {                    // Cancel on the "is debugging this browser" bar: she is out
    release('canceled_by_user');
    cfg.mode = 'off';
    await chrome.storage.local.set({ mode: 'off' });
  }
});

// A click of hers must not put a file on your disk. The debugger cannot refuse
// downloads for an extension, so one that starts right after she acted is
// cancelled here. (A download of your own in those few seconds goes with it.)
chrome.downloads.onCreated.addListener(async (item) => {
  await ready;
  if (!held.size || Date.now() - acted > DOWNLOAD_MS) return;
  try { await chrome.downloads.cancel(item.id); } catch (e) {}
  try { await chrome.downloads.erase({ id: item.id }); } catch (e) {}
  post({ ev: 'note', text: 'a download that your action started was stopped: nothing is saved to the user\'s disk from here' });
});

// A tab opened from one she holds, right after she acted (her click on a link
// that opens a new tab, window.open), is hers too. One YOU open from that tab
// is yours: she has to be given it like any other.
chrome.tabs.onCreated.addListener(async (t) => {
  await ready;
  if (t.openerTabId == null || !held.has(t.openerTabId) || hers.size >= HER_TABS) return;
  if (Date.now() - acted > DOWNLOAD_MS) return;
  hers.add(t.id);
  try { await share(t); } catch (e) {}
  try { post({ ev: 'attached', ...(await attach(t.id, { opener: held.get(t.openerTabId) })) }); } catch (e) { remember(); }
});

chrome.tabs.onUpdated.addListener(async (id, change, tab) => {
  await ready;
  if (!held.has(id) || !(change.url || 'groupId' in change)) return;
  const no = await why(tab);
  if (no) drop(id, no);                                   // it went somewhere, or left the group: she lets go
});

chrome.tabs.onRemoved.addListener(async (id) => {
  await ready;
  if (hers.delete(id) | localOk.delete(id)) remember();
});

// --- the pipe -------------------------------------------------------------------

function origin() {
  try { return new URL(cfg.server).origin; } catch (e) { return ''; }
}

function brand() {
  const b = (navigator.userAgentData && navigator.userAgentData.brands) || [];
  const named = b.find((x) => !/not.?a.?brand|chromium/i.test(x.brand)) || b.find((x) => /chromium/i.test(x.brand));
  return named ? `${named.brand} ${named.version}` : 'Chromium';
}

function setStatus(s) {
  status = { state: s.state, detail: s.detail || '' };
  chrome.storage.session.set({ status });
  chrome.action.setTitle({ title: 'Sapphire: ' + WORDS[status.state] });
}

const WORDS = {
  off: 'not connected', connecting: 'connecting...', online: 'connected',
  lost: 'the connection dropped; trying again', unreachable: 'cannot reach Sapphire at that address',
  refused: 'Sapphire does not know this browser yet',
};

function onPipe(m) {
  if (m.pipe === 'state') setStatus(m);
  else if (m.pipe === 'closed') release('disconnected');  // nobody is driving: every debugger comes off
  else if (m.pipe === 'skew') { cfg.skew = m.skew; chrome.storage.local.set({ skew: m.skew }); }
  else if (m.pipe === 'ready') ensure();
}

chrome.runtime.onConnect.addListener((p) => {
  if (p.name !== 'pipe') return;
  pipe = p;
  p.onDisconnect.addListener(() => {                      // the hidden page is gone, and the socket with it
    if (pipe !== p) return;
    pipe = null;
    ready.then(() => release('disconnected'));
  });
  p.onMessage.addListener(onFrame);
});

let making = null;
async function page() {
  if (await chrome.offscreen.hasDocument()) return;
  making = making || chrome.offscreen.createDocument({
    url: 'pipe.html', reasons: ['WORKERS'], justification: 'Holds the connection to your Sapphire.',
  }).finally(() => { making = null; });
  await making;
}

async function ensure(now) {
  await ready;
  const url = origin() ? origin().replace(/^http/, 'ws') + PIPE_PATH : '';
  if (!url || cfg.mode === 'off') {
    post({ shut: true });
    release('off');
    return setStatus({ state: 'off' });
  }
  await page();
  if (!pipe) chrome.runtime.sendMessage({ wakePipe: true }).catch(() => {});
  for (let i = 0; i < 60 && !pipe; i++) await new Promise((r) => setTimeout(r, 50));
  post({ open: { url, keyId: cfg.keyId, key: cfg.key, skew: cfg.skew || 0, now: !!now } });
}

chrome.storage.onChanged.addListener(async (changes, area) => {
  await ready;
  if (area !== 'local') return;
  for (const k of Object.keys(cfg)) if (changes[k]) cfg[k] = changes[k].newValue === undefined ? cfg[k] : changes[k].newValue;
  if (changes.mode || changes.never) {
    const ours = await groups();
    for (const id of [...held.keys()]) {
      let no = 'closed';
      try { no = await why(await chrome.tabs.get(id), ours); } catch (e) {}
      if (no) drop(id, no);
    }
    post({ ev: 'mode', mode: cfg.mode });
  }
  if (changes.server || changes.key || changes.mode) ensure(true);
});

chrome.alarms.create('keep', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(() => ensure());
chrome.runtime.onStartup.addListener(() => ensure());
chrome.runtime.onInstalled.addListener(() => ensure());
ensure();

// --- pairing --------------------------------------------------------------------
//
// One signal, and nothing else, goes into the tab you are on when you open the
// popup there: the event below. A Sapphire's own page answers it (her Browser
// plugin's web/main.js). "hello" asks whether this page is one; "pair" hands it
// this browser's pairing code, and HER page does the rest (asks you, names the
// device, adds it). "unpair" tells it which pairing this browser just dropped
// (the code's public half), so she can offer to remove the device. So how pairing looks and works can change in Sapphire
// without a new version of this extension. Opening the popup is what lends
// this extension that one tab (activeTab); no page is read.
const SIGNAL = 'sapphire-browser-bridge';
let pairing = 0;                                          // until when a pairing is being waited on

async function signal(tabId, msg) {
  const [r] = await chrome.scripting.executeScript({
    target: { tabId }, args: [SIGNAL, JSON.stringify({ v: 1, ...msg })],
    func: (name, detail) => !document.dispatchEvent(new CustomEvent(name, { detail, cancelable: true })),
  });
  return !!(r && r.result);
}

async function sapphireHere() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab || tab.incognito || !/^https?:/.test(tab.url || '')) return null;
  try {
    return (await signal(tab.id, { ask: 'hello' })) ? { tab: tab.id, origin: new URL(tab.url).origin } : null;
  } catch (e) { return null; }                            // not a page this extension was lent
}

async function hurry() {                                  // she adds the device when you press Allow there
  while (Date.now() < pairing) {
    await new Promise((r) => setTimeout(r, 3000));
    if (status.state === 'online') pairing = 0;
    else if (status.state !== 'connecting' && Date.now() < pairing) await ensure(true);
  }
}

// --- the popup ------------------------------------------------------------------

const ui = {
  async probe() {
    if (status.state === 'online') return null;           // paired and connected: nothing to ask a page
    const here = await sapphireHere();
    return here && { origin: here.origin, replaces: cfg.server && cfg.server !== here.origin ? cfg.server : '' };
  },
  async pair() {
    const here = await sapphireHere();
    if (!here) throw new Error('This tab is not a Sapphire that can pair. Open her page and try there.');
    if (cfg.mode === 'off') await chrome.storage.local.set({ mode: 'shared' });
    await chrome.storage.local.set({ server: here.origin });
    if (!(await signal(here.tab, { ask: 'pair', code: `${cfg.keyId}.${cfg.key}`, browser: brand() }))) {
      throw new Error('That page did not answer. Reload it and try again.');
    }
    const waiting = Date.now() < pairing;
    pairing = Date.now() + 180000;
    if (!waiting) hurry();
    return { origin: here.origin };
  },
  async state() {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    let here = null;
    if (tab) {
      const no = await why(tab, null, 'shared');          // "is THIS tab shared", whatever the switch says
      const address = addressWhy(tab.url || '', -1);      // -1: as if never shared by hand
      here = { id: tab.id, title: tab.title || '', shared: !no, why: no, held: held.has(tab.id),
               local: /own network/.test(address), blocked: address && !/own network/.test(address) ? address : '' };
    }
    return { server: cfg.server, mode: cfg.mode, never: cfg.never, code: `${cfg.keyId}.${cfg.key}`,
             status, words: WORDS[status.state], paired, held: held.size, here, refused: self.refused,
             pairing: Date.now() < pairing };
  },
  async set(m) {
    const put = {};
    if (m.server !== undefined) {
      let text = String(m.server).trim();
      if (text && !/^https?:\/\//i.test(text)) text = 'https://' + text;
      try { put.server = text ? new URL(text).origin : ''; } catch (e) { throw new Error('That is not an address.'); }
    }
    if (m.mode !== undefined && ['off', 'shared', 'all'].includes(m.mode)) put.mode = m.mode;
    if (m.never !== undefined) {                          // through the URL parser: the form a tab's address has
      put.never = String(m.never).split(/[\s,]+/).map((x) => {
        const bare = x.trim().replace(/^[a-z]+:\/\//i, '').replace(/^\*?\./, '').replace(/[/?#].*$/, '');
        try { return host(new URL('http://' + bare).hostname).replace(/^www\./, ''); } catch (e) { return ''; }
      }).filter(Boolean).slice(0, 200);
    }
    await chrome.storage.local.set(put);
    return {};
  },
  async share(m) {
    const tab = await chrome.tabs.get(m.tab);
    if (m.local) localOk.add(tab.id);
    await share(tab);
    remember();
    return {};
  },
  async unshare(m) {
    localOk.delete(m.tab);
    hers.delete(m.tab);
    try { await chrome.tabs.ungroup(m.tab); } catch (e) {}
    drop(m.tab, 'not shared');
    remember();
    return {};
  },
  release() { release('released'); return {}; },
  // Forget the Sapphire this browser is paired with: a new code (the old one is
  // dead, so the device she has can never connect again) and no address.
  async unpair() {
    const here = await sapphireHere();
    let told = false;
    if (here && here.origin === cfg.server) {
      try { told = await signal(here.tab, { ask: 'unpair', id: cfg.keyId }); } catch (e) {}
    }
    pairing = 0;
    release('unpaired');
    await chrome.storage.local.set({ server: '' });
    await mint();
    await ensure(true);
    return { told };
  },
  async retry() { await ensure(true); return {}; },
};

chrome.runtime.onMessage.addListener((m, sender, reply) => {
  if (!m || !m.ui || !ui[m.ui] || sender.id !== chrome.runtime.id) return;
  ready.then(() => ui[m.ui](m)).then((ok) => reply({ ok }), (e) => reply({ err: (e && e.message) || String(e) }));
  return true;
});
