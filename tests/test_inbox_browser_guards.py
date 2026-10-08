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
    assert 'const ownsLive = () => getAbortController() === abortController;' in send
    assert 'let armed = false;' in send
    assert "onQueued: (ticket, position, chat) =>" in send and 'api.dropQueued(myTicket, myChat)' in send
    fin = send.split('} finally {')[1]
    assert 'if (armed && ownsLive())' in fin
    stop = sh.split('export async function handleStop')[1].split('\nexport ')[0]
    assert 'if (getAbortController() === controller)' in stop


def test_the_queued_event_names_its_chat_and_the_drop_sends_it():
    api = _src('api.js')
    assert 'handlers.onQueued(data.ticket, data.position, data.chat)' in api
    assert 'export const dropQueued = (ticket, chat = null)' in api
    route = (Path(__file__).resolve().parent.parent / 'core' / 'routes' / 'chat.py').read_text(encoding='utf-8')
    assert "'chat': chat_key" in route.split("'type': 'queued'")[1].split('\n')[0]


def test_the_inbox_chip_reads_its_chat_at_click_time():
    js = _src('features/agent-status.js')
    assert "openInboxCard(chip, chip.dataset.chat || chat)" in js and "chip.dataset.chat = chat || ''" in js
