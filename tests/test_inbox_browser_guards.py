"""Source guards for the browser side of the inbox (race scout, 2026-10-07):
the inbox starts the next turn within milliseconds of the last one ending,
and the browser must not assume it owns the chat's timeline."""
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / 'interfaces' / 'web' / 'static'


def _src(rel):
    return (STATIC / rel).read_text(encoding='utf-8')


def test_the_finish_swap_takes_this_turns_assistant_row_not_the_last_row():
    ui = _src('ui.js')
    body = ui.split('export const finishStreaming')[1].split('\n};')[0]
    assert "while (li > 0 && hist[li]?.role !== 'assistant') li--;" in body
    assert 'const lastMsg = hist[hist.length - 1];' not in body
    # a card rebuilt from history is re-locked when the user already answered
    assert 'lockAnsweredAskCards(chat);' in body.split('replaceWith(clone)')[1]


def test_the_server_decides_queued_and_the_live_slot_is_owned():
    sh = _src('handlers/send-handlers.js')
    send = sh.split('export async function handleSend')[1].split('\nexport ')[0]
    assert 'const queuedSend = getIsProc()' not in send, 'the browser predicted queued state again'
    assert 'const ownsLive = () => getAbortController() === ctl.controller;' in send
    assert "onQueued: (ticket, position, chat) =>" in send and 'api.dropQueued(ctl.ticket, ctl.chat)' in send
    # release() gives back only what the send still owns; a send that is waiting
    # or following keeps owning past its first socket (2026-10-08)
    rel = send.split('const release = () => {')[1].split('};')[0]
    assert 'if (ctl.armed && ownsLive())' in rel and 'pending.delete(ctl.ticket)' in rel
    fin = send.split('} finally {')[-1]
    assert "if (ctl.state === 'queued' || ctl.state === 'lost' || ctl.state === 'starting')" in fin
    # a follow never arms a dead send: the slot moves only if it held the old controller
    assert 'if (ctl.armed && getAbortController() === old) setAbortController(ctl.controller);' in send
    stop = sh.split('export async function handleStop')[1].split('\nexport ')[0]
    assert 'if (getAbortController() === controller)' in stop


def test_a_queued_send_closes_its_socket_and_the_bus_wakes_the_owner():
    """Six held sockets hit the browser's per-host cap (two buses + a live turn
    + three queued sends): Stop and × stalled. A queued send CLOSES after the
    `queued` line; the owner follows its ticket when the bus says `started`."""
    api = _src('api.js')
    q = api.split("if (data.type === 'queued')")[1].split('}')[0]
    assert 'shouldReturn: true' in q
    assert 'if (res.status === 202)' in api and 'queued: true' in api
    sh = _src('handlers/send-handlers.js')
    assert "on('inbox_ticket', (d) => {" in sh and "if (d.state === 'started') ctl.started();" in sh
    assert "on('bus_connected', checkPending);" in sh and "document.addEventListener('visibilitychange'" in sh
    route = (Path(__file__).resolve().parent.parent / 'core' / 'routes' / 'chat.py').read_text(encoding='utf-8')
    assert 'KEEPALIVE_S = 15.0' in route and '": keepalive' not in route.split('def _turn_response')[1].split('for event in')[0]
    assert "publish('inbox_ticket', {'ticket': ticket, 'state': state, 'reason': reason}, ephemeral=True)" in route
    assert "viewer = turn.attach(audio=True) if sock['waiting'] else None" in route


def test_the_queued_event_names_its_chat_and_the_drop_sends_it():
    api = _src('api.js')
    assert 'handlers.onQueued(data.ticket, data.position, data.chat)' in api
    assert 'export const dropQueued = (ticket, chat = null)' in api
    route = (Path(__file__).resolve().parent.parent / 'core' / 'routes' / 'chat.py').read_text(encoding='utf-8')
    assert "'chat': chat_key" in route.split("'type': 'queued'")[1].split('\n')[0]


def test_the_inbox_chip_reads_its_chat_at_click_time():
    js = _src('features/agent-status.js')
    assert "openInboxCard(chip, chip.dataset.chat || chat)" in js and "chip.dataset.chat = chat || ''" in js


def test_queued_bubbles_survive_the_history_reconcile_and_a_moved_view_paints_nothing():
    """The refresh at the turn-ahead's llm_done deleted the pulsing queued
    bubble (no key = stale); a queued turn painted and swapped into whichever
    chat the view had moved to (two scouts, 2026-10-07)."""
    ui = _src('ui.js')
    body = ui.split('export const renderHistory')[1].split('\n};')[0]
    assert "const inflight = existing.slice(keep).filter(el => el.classList.contains('queued') && el.dataset.ticket);" in body
    assert 'for (const el of inflight) chat.appendChild(el);' in body
    sh = _src('handlers/send-handlers.js')
    send = sh.split('export async function handleSend')[1].split('\nexport ')[0]
    assert "if (viewMoved()) { ctl.headless = true; return; }" in send
    assert 'if (ctl.headless || !streamStillMine()) return;' in send
    assert "ui.showToast(`Her reply landed in “${ctl.chat}”`, 'info');" in send
    app = (Path(__file__).resolve().parent.parent / 'plugins' / 'agents' / 'app' / 'index.js').read_text(encoding='utf-8')
    assert "if (prev && prev.dataset.sig === sig(a)) { next.appendChild(prev); continue; }" in app
