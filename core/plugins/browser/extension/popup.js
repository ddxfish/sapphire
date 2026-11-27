// popup.js - the switch, the tab you are on, and the pairing. All state lives
// in the worker; this page only shows it and sends what you pressed.
const $ = (id) => document.getElementById(id);

async function ask(ui, more) {
  const r = await chrome.runtime.sendMessage({ ui, ...more });
  if (!r) throw new Error('The extension is restarting; open this again.');
  if (r.err) throw new Error(r.err);
  return r.ok;
}

const MODE_HELP = {
  off: 'She is disconnected from this browser.',
  shared: 'Only tabs in the "Sapphire" tab group, and tabs she opens herself.',
  all: 'Every tab, except sites you never share, pages on your own network, and private windows.',
};

const DETAIL = {
  off: (s) => (s.server ? '' : 'Open your Sapphire in a tab and press this button there.'),
  refused: (s) => (s.pairing ? 'Asked. Press Allow in your Sapphire tab.'
    : 'To pair: open your Sapphire in a tab and press this button there.'),
  unreachable: (s) => `Nothing answered at ${s.server}. If she uses her own certificate, open that address in a tab once and accept it.`,
  online: (s) => (s.paired.name ? `Paired as "${s.paired.label || s.paired.name}".` : ''),
};

let last = null;
let found = null;                                         // this tab, when it is a Sapphire that can pair this browser

async function show() {
  const s = last = await ask('state');
  $('dot').className = s.status.state;
  $('words').textContent = s.words;
  $('detail').textContent = (DETAIL[s.status.state] || (() => ''))(s);
  for (const b of $('mode').children) b.classList.toggle('on', b.dataset.mode === s.mode);
  $('modehelp').textContent = MODE_HELP[s.mode];
  if (document.activeElement !== $('server')) $('server').value = s.server;
  $('code').value = s.code;
  if (document.activeElement !== $('never')) $('never').value = s.never.join('\n');
  if (s.status.state === 'unreachable' && !found) $('setup').open = true;
  $('pairsec').hidden = !found || s.status.state === 'online';
  if (found) {
    $('pairwords').textContent = s.pairing ? 'Asked. Press Allow in this tab.'
      : `This tab is a Sapphire at ${found.origin}.` + (found.replaces ? ` Pairing here replaces ${found.replaces}.` : '');
    $('pair').textContent = s.pairing ? 'Ask again' : 'Pair this browser';
  }

  const h = s.here;
  $('heresec').hidden = !h;
  if (h) {
    $('here').textContent = h.blocked ? `This tab: ${h.blocked}.`
      : h.shared ? (h.held ? 'This tab: she is using it.' : 'This tab is shared.')
      : s.mode === 'all' && !h.local ? 'This tab: usable (all tabs).' : 'This tab is not shared.';
    $('share').textContent = h.shared ? 'Stop sharing' : 'Share this tab';
    $('share').disabled = !!h.blocked;
  }
  $('heldrow').hidden = !s.held;
  $('heldwords').textContent = `Sapphire is using ${s.held} tab${s.held === 1 ? '' : 's'}.`;
}

function note(text) { $('note').textContent = text || ''; }

// A question asked in the popup itself. The browser's own confirm() opens a
// second box inside this small window, with its buttons half out of reach.
function sure(text, yes) {
  return new Promise((resolve) => {
    const done = (answer) => { $('asksec').hidden = true; resolve(answer); };
    $('askwords').textContent = text;
    $('askyes').textContent = yes;
    $('askyes').onclick = () => done(true);
    $('askno').onclick = () => done(false);
    $('asksec').hidden = false;
    $('asksec').scrollIntoView({ block: 'nearest' });
  });
}

async function act(fn) {
  try { note(''); await fn(); } catch (e) { note(e.message); }
  setTimeout(() => show().catch(() => {}), 150);
}

$('mode').addEventListener('click', (e) => {
  const m = e.target.dataset.mode;
  if (m) act(() => ask('set', { mode: m }));
});
$('share').addEventListener('click', () => act(async () => {
  const h = last.here;
  if (h.shared) return ask('unshare', { tab: h.id });
  if (h.local && !(await sure('This page is on your own network. Share it with Sapphire?', 'Share it'))) return null;
  return ask('share', { tab: h.id, local: h.local });
}));
$('pair').addEventListener('click', () => act(() => ask('pair')));
$('release').addEventListener('click', () => act(() => ask('release')));
$('save').addEventListener('click', () => act(() => ask('set', { server: $('server').value })));
$('saveNever').addEventListener('click', () => act(() => ask('set', { never: $('never').value })));
$('retry').addEventListener('click', () => act(() => ask('retry')));
$('unpair').addEventListener('click', () => act(async () => {
  if (!(await sure('Unpair this browser? Sapphire can no longer use it until you pair again.', 'Unpair'))) return;
  const r = await ask('unpair');
  found = await ask('probe').catch(() => null);
  note(r.told ? 'Unpaired. Sapphire asks on her page whether to remove the device too.'
    : 'Unpaired. Its device stays in Sapphire until you delete it in Settings > Devices.');
}));
$('copy').addEventListener('click', () => act(async () => {
  await navigator.clipboard.writeText($('code').value);
  note('Copied. Paste it into the Browser device in Sapphire.');
}));

ask('probe').then((p) => { found = p; }, () => {}).then(() => show()).catch((e) => note(e.message));
const tick = setInterval(() => show().catch(() => {}), 1500);
addEventListener('unload', () => clearInterval(tick));
