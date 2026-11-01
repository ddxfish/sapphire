"""core/text_extract.py: the one way bytes become text (tmp/documents-plan.md)."""
import io

import pytest
from PIL import Image
from pypdf import PdfWriter

from core import text_extract as tx


def _pdf_of_images(*sizes):
    """Image-only pages, the way a scanner makes them."""
    pages = [Image.new('RGB', s, (200, 220, 240)) for s in sizes]
    buf = io.BytesIO()
    pages[0].save(buf, 'PDF', save_all=True, append_images=pages[1:])
    return buf.getvalue()


def _blank_pdf(password=None):
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    if password:
        w.encrypt(password)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_decode_drops_bom_and_reads_cp1252():
    assert tx.decode_bytes('﻿hi'.encode('utf-8')) == 'hi'
    assert tx.decode_bytes(b'Krem\x92s') == 'Krem’s'


def test_decode_text_normalizes_newlines():
    assert tx.decode_text(b'a\r\n\r\nb\rc') == ('a\n\nb\nc', None)


@pytest.mark.parametrize('raw', [b'PK\x03\x04\x00\x00zipzip', b'\x89PNG\r\n\x1a\n\x00\x00'])
def test_binary_is_refused_not_embedded_as_junk(raw):
    text, err = tx.decode_text(raw)
    assert text is None and 'binary' in err


def test_utf16_is_text_not_binary():
    """Notepad's "Unicode" and PowerShell's `>` write this on Windows."""
    assert tx.decode_text('café\r\nb'.encode('utf-16')) == ('café\nb', None)


def test_scan_is_empty_pages_not_a_refusal_but_extract_pdf_refuses():
    raw = _pdf_of_images((300, 400), (300, 400))
    pages, _, err = tx.pdf_pages(raw)
    assert err is None and pages == ['', '']
    assert not tx.has_text_layer(pages)
    text, _, err = tx.extract_pdf(raw)
    assert text is None and 'text layer' in err


def test_password_pdf_is_refused_in_words():
    pages, _, err = tx.pdf_pages(_blank_pdf('secret'))
    assert pages is None and 'password' in err
    assert 'password' in tx.extract_pdf(_blank_pdf('secret'))[2]


def test_corrupt_pdf_is_refused_in_words():
    assert 'parse failed' in tx.pdf_pages(b'%PDF-1.7 not really')[2]


def test_page_pictures_gives_jpegs_from_a_scan():
    pics, why = tx.page_pictures(_pdf_of_images((300, 400), (500, 600)), 2)
    assert why is None and len(pics) == 1
    data, mime = pics[0]
    assert mime == 'image/jpeg'
    assert Image.open(io.BytesIO(data)).size == (500, 600)


def test_page_pictures_shrinks_a_huge_scan():
    pics, _ = tx.page_pictures(_pdf_of_images((4000, 3000)), 1)
    assert max(Image.open(io.BytesIO(pics[0][0])).size) == tx.PICTURE_MAX_SIDE


def test_page_pictures_says_why_when_there_is_nothing_to_show():
    raw = _blank_pdf()
    pics, why = tx.page_pictures(raw, 1)
    assert pics == [] and 'vector' in why
    pics, why = tx.page_pictures(raw, 9)
    assert pics == [] and 'no page 9' in why


def test_page_pictures_names_a_jbig2_failure(monkeypatch):
    class Bad:
        @property
        def image(self):
            raise RuntimeError('jbig2dec binary is not available.')

    class Found(dict):
        pass

    class Page:
        images = Found(a=Bad())

    class Reader:
        pages = [Page()]

    monkeypatch.setattr(tx, '_open_pdf', lambda raw: Reader())
    pics, why = tx.page_pictures(b'', 1)
    assert pics == [] and 'jbig2dec' in why


def test_html_to_text_keeps_the_heading_out_of_the_body():
    text, head = tx.html_to_text('<h1>One</h1><script>x</script><p>a</p><p>b</p>')
    assert head == 'One' and 'x' not in text and 'a' in text and 'One' not in text
