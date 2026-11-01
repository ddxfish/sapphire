"""core/documents.py: one chat's files, what a turn gets of them, and her way to the
rest (tmp/documents-plan.md). Each bug the rework fixed is pinned here."""
import io
import sqlite3

import numpy as np
import pytest
from PIL import Image
from pypdf import PdfWriter

from core import documents as docs
from plugins.memory.tools import knowledge_tools as kb


class FakeEmbedder:
    """Same word, same direction: enough to rank by shared vocabulary."""
    available = True
    provider_id = 'fake'

    def embed(self, texts, prefix=None):
        out = []
        for t in texts:
            v = np.zeros(64, dtype=np.float32)
            for w in t.lower().split():
                v[hash(w) % 64] += 1
            n = np.linalg.norm(v)
            out.append(v / n if n else v)
        return np.array(out)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(kb, '_db_path', tmp_path / 'knowledge.db')
    monkeypatch.setattr(kb, '_db_initialized', False)
    monkeypatch.setattr(kb, '_backfill_done', True)
    monkeypatch.setattr(kb, '_get_embedder', lambda: FakeEmbedder())
    monkeypatch.setattr(docs, '_dir', lambda: tmp_path / 'documents')
    monkeypatch.setattr(docs, '_heal_later', lambda chat: None)
    monkeypatch.setattr(docs, '_changed', lambda chat: None)
    return tmp_path


def _scan(pages=2):
    imgs = [Image.new('RGB', (300, 400), (200, 220, 240)) for _ in range(pages)]
    buf = io.BytesIO()
    imgs[0].save(buf, 'PDF', save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def _text_pdf():
    return _pdf_with_text()


def _pdf_with_text():
    """A tiny hand-made PDF with a real text layer."""
    stream = b"BT /F1 12 Tf 20 100 Td (" + b"words on a page that has a real text layer " * 2 + b") Tj ET"
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Count 1/Kids[3 0 R]>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 800 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % n for n in offs)
    return out + b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % x


def _long_text(paragraphs=200, needle=None, at=150):
    out = [f"Paragraph {i} talks about ordinary filler matters " + "word " * 59 + "word" for i in range(paragraphs)]
    if needle:
        out[at] = needle
    return '\n\n'.join(out).encode()


def test_add_list_remove(store):
    assert docs.add('c', 'a.txt', b'hello world')['chunks'] == 1
    got = docs.listing('c')
    assert [(d['filename'], d['status']) for d in got] == [('a.txt', 'searchable')]
    assert docs.remove('c', 'a.txt') is True
    assert docs.listing('c') == []
    assert docs.remove('c', 'a.txt') is False


def test_path_in_a_name_is_dropped(store):
    assert docs.add('c', '..\\..\\evil/x.txt', b'hi')['filename'] == 'x.txt'


def test_binary_refused_and_stores_nothing(store):
    with pytest.raises(docs.Refused, match='binary'):
        docs.add('c', 'a.docx', b'PK\x03\x04\x00\x00')
    assert docs.listing('c') == []


def test_too_large_refused(store, monkeypatch):
    monkeypatch.setattr(docs, 'MAX_BYTES', 4)
    with pytest.raises(docs.Refused, match='too large'):
        docs.add('c', 'a.txt', b'12345')


def test_crlf_file_splits_into_paragraphs(store):
    text = '\r\n\r\n'.join("para " + "word " * 300 for _ in range(4)).encode()
    assert docs.add('c', 'w.txt', text)['chunks'] >= 3


def test_no_embedder_still_stores_says_keyword_and_keyword_finds(store, monkeypatch):
    monkeypatch.setattr(kb, '_get_embedder', lambda: None)
    docs.add('c', 'n.txt', _long_text(needle="the widget serial is XK-4471 today"))
    assert docs.listing('c')[0]['status'] == 'keyword'
    assert 'XK-4471' in docs.search('c', 'XK-4471')


def test_other_providers_vectors_read_as_keyword(store, monkeypatch):
    docs.add('c', 'a.txt', b'hello world')
    other = FakeEmbedder()
    other.provider_id = 'other'
    monkeypatch.setattr(kb, '_get_embedder', lambda: other)
    assert docs.listing('c')[0]['status'] == 'keyword'


def test_heal_embeds_what_was_missing(store, monkeypatch):
    monkeypatch.setattr(kb, '_get_embedder', lambda: None)
    docs.add('c', 'a.txt', b'hello world')
    monkeypatch.setattr(kb, '_get_embedder', lambda: FakeEmbedder())
    docs._healing.add('c')
    docs._heal('c')
    assert docs.listing('c')[0]['status'] == 'searchable'
    assert 'c' not in docs._healing


def test_failed_reupload_keeps_the_old_copy(store, monkeypatch):
    docs.add('c', 'a.txt', b'the original words')
    monkeypatch.setattr(docs, '_embed', lambda chunks, report=None: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        docs.add('c', 'a.txt', b'replacement words')
    assert 'original' in docs.read('c', 'a.txt')[0]


def test_reupload_replaces_in_place(store):
    docs.add('c', 'a.txt', b'first version')
    docs.add('c', 'a.txt', b'second version')
    assert len(docs.listing('c')) == 1
    text = docs.read('c', 'a.txt')[0]
    assert 'second' in text and 'first' not in text


def test_parts_rebuild_the_text_in_order(store):
    raw = _long_text(50)
    n = docs.add('c', 'a.txt', raw)['chunks']
    assert n > 3
    tab = docs._find('c', 'a.txt')[0]
    assert '\n\n'.join(docs._parts(tab)).encode() == raw.strip()


def test_small_file_arrives_whole(store):
    docs.add('c', 'code.py', b'def broken():\n    return 1/0\n')
    text, pics = docs.context('c', 'what is wrong', 'normal')
    assert text.startswith('[Reference Documents]')
    assert '1/0' in text and 'in full' in text and pics == []


def test_off_is_nothing(store):
    docs.add('c', 'a.txt', b'hello')
    assert docs.context('c', 'hello', 'off') == (None, [])


def test_no_documents_is_nothing(store):
    assert docs.context('c', 'hello', 'normal') == (None, [])


def test_big_file_gets_excerpts_within_budget_and_says_how_to_get_more(store):
    from core.chat.budget import count_tokens
    docs.add('c', 'big.txt', _long_text(needle="the serial number is XK-4471"))
    text, _ = docs.context('c', 'XK-4471', 'light')
    assert 'XK-4471' in text and 'read_attachment' in text and 'part' in text
    assert count_tokens(text) < docs.LEVELS['light'] * 1.25


def test_levels_are_budgets_heavy_gives_more(store):
    from core.chat.budget import count_tokens
    docs.add('c', 'big.txt', _long_text(600))
    light = count_tokens(docs.context('c', 'word word word', 'light')[0])
    heavy = count_tokens(docs.context('c', 'word word word', 'heavy')[0])
    assert heavy > light * 3


def test_budget_capped_by_the_window():
    assert docs._budget_for('heavy', 8000) == 2000
    assert docs._budget_for('light', 0) == 2000


def test_small_local_model_is_never_sent_more_than_it_holds(store):
    """A 50KB document on an 8K window used to fail every turn, the same way, forever."""
    from core.chat.budget import count_tokens
    docs.add('c', 'big.txt', _long_text())
    text, _ = docs.context('c', 'word word', 'normal', window=8192)
    assert count_tokens(text) < 8192 * docs.WINDOW_SHARE * 1.25


def test_blind_model_gets_words_not_placeholders(store):
    docs.add('c', 'scan.pdf', _scan(1))
    text, pics = docs.context('c', 'x', 'normal', sees=False)
    assert pics == [] and "can't see pictures" in text and 'read_attachment' in text
    assert 'read_attachment' not in docs.context('c', 'x', 'normal', tools=False, sees=False)[0]


def test_a_full_page_is_counted_at_its_real_cost(store):
    docs.add('c', 'three.pdf', _scan(3))     # 3 x 4000: too dear for Normal's 8000
    assert docs.context('c', 'x', 'normal')[1] == []
    assert len(docs.context('c', 'x', 'heavy')[1]) == 3


def test_chat_gone_mid_upload_leaves_no_ghost(store):
    with pytest.raises(docs.Refused, match='renamed or deleted'):
        docs.add('c', 'a.txt', b'hello', still_there=lambda: False)
    assert docs.listing('c') == []


def test_rename_clears_a_ghost_under_the_new_name(store):
    docs.add('new', 'ghost.txt', b'left behind')
    docs.add('old', 'real.txt', b'the real one')
    docs.rename_chat('old', 'new')
    assert [d['filename'] for d in docs.listing('new')] == ['real.txt']


def test_sweep_takes_dead_chats_and_stray_files_keeps_the_living(store):
    docs.add('alive', 'keep.pdf', _scan(1))
    docs.add('dead', 'gone.pdf', _scan(1))
    keep, gone = docs._original(docs._find('alive', 'keep.pdf')[0]), docs._original(docs._find('dead', 'gone.pdf')[0])
    stray = docs._dir() / '999999.pdf'
    stray.write_bytes(b'x')
    (docs._dir() / 'abc.tmp').write_bytes(b'x')
    docs.sweep_orphans(['alive'])
    assert keep.exists() and not gone.exists() and not stray.exists() and not (docs._dir() / 'abc.tmp').exists()
    assert docs.listing('dead') == [] and len(docs.listing('alive')) == 1


def test_scan_write_failure_keeps_the_old_copy(store, monkeypatch):
    docs.add('c', 'a.pdf', _scan(1))
    monkeypatch.setattr(docs, '_dir', lambda: store / 'nope' / 'file')
    (store / 'nope').write_bytes(b'')    # the folder can't be made
    with pytest.raises(docs.Refused, match='on disk'):
        docs.add('c', 'a.pdf', _scan(2))
    assert docs.listing('c')[0]['pages'] == 1


def test_failed_move_never_leaves_old_pictures_under_new_text(store, monkeypatch):
    w = _text_pdf()
    docs.add('c', 'a.pdf', _scan(1))
    path = docs._original(docs._find('c', 'a.pdf')[0])
    monkeypatch.setattr(docs, 'replace_with_retry', lambda a, b: (_ for _ in ()).throw(PermissionError('held')))
    docs.add('c', 'a.pdf', w)
    assert not path.exists() and not list(docs._dir().glob('*.tmp'))


def test_unreadable_scan_is_refused_up_front(store, monkeypatch):
    monkeypatch.setattr(docs.tx, 'page_pictures', lambda raw, n: ([], 'needs jbig2dec'))
    with pytest.raises(docs.Refused, match='jbig2dec'):
        docs.add('c', 'a.pdf', _scan(1))
    assert docs.listing('c') == []


def test_long_name_keeps_its_extension():
    assert docs._clean_name('x' * 300 + '.pdf').endswith('.pdf')


def test_insert_goes_before_the_persons_row_with_clean_words():
    from types import SimpleNamespace
    from core.chat.chat import LLMChat
    seen = {}
    me = SimpleNamespace(_get_rag_context=lambda w, h, p, t: seen.update(w=w, h=h) or ('[Reference Documents]\nx', [(b'j', 'image/jpeg')]))
    live = {'role': 'user', 'content': 'q'}
    msgs = [{'role': 'system', 'content': 's'}, live, {'role': 'assistant', 'content': 'partial'}]
    LLMChat.insert_documents(me, msgs, live, 'q', None, True)
    assert [m['role'] for m in msgs] == ['system', 'user', 'user', 'user', 'assistant']
    assert msgs[3] is live and msgs[1]['_inject'] and msgs[2]['content'][0]['text'].startswith('[Pages of')
    assert seen['w'] == 'q' and len(seen['h']) == 1


def test_no_tools_means_no_tool_advice(store):
    docs.add('c', 'big.txt', _long_text())
    assert 'read_attachment' not in docs.context('c', 'filler', 'light', tools=False)[0]


def test_a_miss_still_lists_the_file(store):
    docs.add('c', 'big.txt', _long_text())
    text, _ = docs.context('c', 'zebra', 'light')
    assert 'big.txt' in text and 'read_attachment' in text


def test_the_5000_line_case_she_can_walk_to_it(store):
    docs.add('c', 'big.txt', _long_text(needle="the launch code is hidden here"))
    parts = docs.listing('c')[0]['chunks']
    found, part = False, 1
    while part <= parts and not found:
        text, _ = docs.read('c', 'big.txt', part=part, count=10)
        found = 'launch code' in text
        part += 10
    assert found


def test_read_past_the_end_and_unknown_file_say_so(store):
    docs.add('c', 'a.txt', b'hello')
    assert 'no part 9' in docs.read('c', 'a.txt', part=9)[0]
    assert 'a.txt' in docs.read('c', 'nope.txt')[0]


def test_read_finds_a_loose_name(store):
    docs.add('c', 'Report Final.txt', b'hello')
    assert 'hello' in docs.read('c', 'report')[0]


def test_scan_is_kept_and_shows_pictures(store):
    made = docs.add('c', 'scan.pdf', _scan(2))
    assert made['kind'] == 'scan' and made['pages'] == 2
    assert docs.listing('c')[0]['status'] == 'pictures'
    text, pics = docs.read('c', 'scan.pdf', page=2)
    assert 'page 2 of 2' in text and len(pics) == 1


def test_small_scan_fits_whole_big_scan_is_listed(store):
    docs.add('c', 'small.pdf', _scan(2))
    text, pics = docs.context('c', 'x', 'normal')
    assert len(pics) == 2 and 'small.pdf' in text
    docs.remove('c', 'small.pdf')
    docs.add('c', 'big.pdf', _scan(20))
    text, pics = docs.context('c', 'x', 'normal')
    assert pics == [] and 'page=N' in text and '20 pages' in text


def test_text_pdf_page_lookup(store):
    assert docs.add('c', 't.pdf', _pdf_with_text())['kind'] == 'pdf'
    text, pics = docs.read('c', 't.pdf', page=1)
    assert 'real text layer' in text and pics == [] and 'vector' not in text
    assert 'real text layer' in docs.read('c', 't.pdf')[0]


def test_blank_pdf_with_nothing_to_show_is_refused(store):
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(docs.Refused, match='vector'):
        docs.add('c', 'blank.pdf', buf.getvalue())


def test_not_a_pdf_has_no_pages(store):
    docs.add('c', 'a.txt', b'hello')
    assert 'no pages' in docs.read('c', 'a.txt', page=1)[0]


def test_older_document_without_a_stored_pdf_says_so(store):
    docs.add('c', 'scan.pdf', _scan(1))
    docs._original(docs._find('c', 'scan.pdf')[0]).unlink()
    assert 'attach' in docs.read('c', 'scan.pdf', page=1)[0]


def test_every_delete_takes_the_pdf(store):
    for how in (lambda: docs.remove('c', 'scan.pdf'), lambda: docs.delete_chat('c')):
        docs.add('c', 'scan.pdf', _scan(1))
        path = docs._original(docs._find('c', 'scan.pdf')[0])
        assert path.exists()
        how()
        assert not path.exists() and docs.listing('c') == []


def test_rename_keeps_documents_and_pictures(store):
    docs.add('old', 'scan.pdf', _scan(1))
    docs.rename_chat('old', 'new')
    assert docs.listing('old') == []
    assert len(docs.read('new', 'scan.pdf', page=1)[1]) == 1


def test_one_chat_never_sees_anothers(store):
    docs.add('a', 'secret.txt', b'the secret is here')
    assert docs.context('b', 'secret', 'normal') == (None, [])
    assert 'secret.txt' not in docs.read('b', 'secret.txt')[0].split('has:')[0].replace("'secret.txt'", '')
    assert docs.search('b', 'secret').startswith('No searchable')


def test_tool_reads_the_turns_chat_only(store):
    from core.chat.function_manager import scope_rag
    import functions.documents as tool
    docs.add('a', 'secret.txt', b'the secret is here')
    token = scope_rag.set('__rag__:b')
    try:
        assert 'the secret' not in tool.execute('read_attachment', {'file': 'secret.txt'}, None)[0]
    finally:
        scope_rag.reset(token)
    token = scope_rag.set('__rag__:a')
    try:
        assert 'the secret' in tool.execute('read_attachment', {'file': 'secret.txt'}, None)[0]
    finally:
        scope_rag.reset(token)
    assert 'No documents' in tool.execute('search_attachments', {'query': 'x'}, None)[0]


def test_tool_returns_a_page_as_an_image_result(store):
    from core.chat.function_manager import scope_rag
    import functions.documents as tool
    docs.add('a', 'scan.pdf', _scan(1))
    token = scope_rag.set('__rag__:a')
    try:
        got, ok = tool.execute('read_attachment', {'file': 'scan.pdf', 'page': 1}, None)
    finally:
        scope_rag.reset(token)
    assert ok and got['images'][0]['media_type'] == 'image/jpeg' and 'page 1' in got['text']


def _tool(name):
    return {'function': {'name': name}}


ALL = [_tool('web'), _tool('read_attachment'), _tool('search_attachments')]


def test_tools_appear_with_documents_whatever_the_toolset(store):
    assert [t['function']['name'] for t in docs.fence_tools([_tool('web')], 'c', ALL)] == ['web']
    docs.add('c', 'a.txt', b'hello')
    names = [t['function']['name'] for t in docs.fence_tools([_tool('web')], 'c', ALL)]
    assert names == ['web', 'read_attachment', 'search_attachments']


def test_tools_leave_without_documents_or_when_off(store):
    assert [t['function']['name'] for t in docs.fence_tools(ALL, 'c', ALL)] == ['web']
    docs.add('c', 'a.txt', b'hello')
    assert [t['function']['name'] for t in docs.fence_tools(ALL, 'c', ALL, 'off')] == ['web']


def test_toolset_none_stays_none(store):
    docs.add('c', 'a.txt', b'hello')
    assert docs.fence_tools([], 'c', ALL) == []


def test_old_style_document_still_lists_and_reads(store):
    """Stored before this module: description NULL, chunks with overlap."""
    kb.create_scope('__rag__:c')
    tab = kb.create_tab('old.txt', scope='__rag__:c', tab_type='user')
    kb.add_entry(tab, 'old words', chunk_index=0, source_filename='old.txt')
    got = docs.listing('c')[0]
    assert got['filename'] == 'old.txt' and got['kind'] == 'text' and got['status'] == 'searchable'
    assert 'old words' in docs.context('c', 'old', 'normal')[0]


def _user(handle=None, text='hi'):
    if handle:
        return {'role': 'user', 'content': [{'type': 'text', 'text': text}, {'type': 'image', 'handle': f'img:{handle}'}]}
    return {'role': 'user', 'content': text}


def test_images_in_view_counts_down_and_matches_the_wire():
    """Whatever the row says she sees is what _replay_images really re-sends."""
    from core.chat.history import images_in_view, _replay_images
    msgs = [_user('a.jpg'), {'role': 'assistant', 'content': 'ok'}]
    assert images_in_view(msgs, 3) == [{'id': 'a.jpg', 'turns': 3, 'source': 'you'}]
    for left in (2, 1, 0):
        msgs += [_user(), {'role': 'assistant', 'content': 'ok'}]
        got = images_in_view(msgs, 3)
        assert got == ([{'id': 'a.jpg', 'turns': left, 'source': 'you'}] if left else [])
        wire = [{'role': m['role'], 'content': 'x'} for m in msgs]
        refs = [[('id', 'a.jpg')] if m is msgs[0] else [] for m in msgs]
        sent = _replay_images(wire, refs, 3, lambda i: (b'x', 'image/jpeg'))
        assert isinstance(sent[0]['content'], list) is bool(left)


def test_images_in_view_zero_window_and_tool_pictures():
    from core.chat.history import images_in_view
    msgs = [_user(), {'role': 'tool', 'tool_call_id': '1', 'content': 'sheet <<IMG::tool:t.jpg>>'}]
    assert images_in_view(msgs, 0) == []
    assert images_in_view(msgs, 2) == [{'id': 't.jpg', 'turns': 2, 'source': 'tool'}]


def test_claude_cache_marker_skips_the_scanned_pages_row():
    """The marker on a row that changes every turn means the cache never gets read."""
    src = open('core/chat/llm_providers/claude.py', encoding='utf-8').read()
    assert '[Pages of the scanned documents' in src
    assert '[Pages of the scanned documents' in open('core/chat/chat.py', encoding='utf-8').read()


def test_tool_names_collide_with_no_plugin():
    """A taken name refuses the WHOLE plugin at load: this once stopped Mind Palace."""
    import json
    from pathlib import Path
    for manifest in Path('core/plugins').glob('*/plugin.json'):
        for rel in json.loads(manifest.read_text(encoding='utf-8')).get('capabilities', {}).get('tools', []):
            src = (manifest.parent / rel).read_text(encoding='utf-8')
            for name in docs.TOOL_NAMES:
                assert f'"{name}"' not in src and f"'{name}'" not in src, f'{manifest.parent.name} has {name}'


def _b64_image(fmt, mode='RGB'):
    import base64
    buf = io.BytesIO()
    Image.new(mode, (20, 20), (200, 30, 30) if mode == 'RGB' else (200, 30, 30, 90)).save(buf, fmt)
    return base64.b64encode(buf.getvalue()).decode()


def test_webp_goes_out_as_something_a_local_server_decodes():
    """LM Studio: "'url' field must be a base64 encoded image", on WebP only."""
    import base64
    from core.images import wire_safe
    data, mt = wire_safe(_b64_image('WEBP'), 'image/webp')
    assert mt == 'image/jpeg' and Image.open(io.BytesIO(base64.b64decode(data))).format == 'JPEG'
    data, mt = wire_safe(_b64_image('WEBP', 'RGBA'), 'image/webp')
    assert mt == 'image/png'        # transparency survives
    jpeg = _b64_image('JPEG')
    assert wire_safe(jpeg, 'image/jpeg') == (jpeg, 'image/jpeg')
    assert wire_safe('not base64!', 'image/webp') == ('not base64!', 'image/webp')


def test_a_refused_file_does_not_blame_the_model():
    from core.chat.llm_errors import explain
    w = explain(_status_error("Error code: 400 - {'error': \"'url' field must be a base64 encoded image.\"}"))
    assert "can't see images" not in w['text'] and 'JPEG or PNG' in w['text']
    assert "can't see images" in explain(_status_error("Error code: 400 - this model does not support image input"))['text']


def _status_error(words):
    e = Exception(words)
    e.status_code = 400
    return e
