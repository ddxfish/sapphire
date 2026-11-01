# core/text_extract.py - bytes in, text or an honest refusal out. No database, no
# plugin imports: chat documents, the Library and the classic knowledge upload all
# read files through here (tmp/documents-plan.md). What a caller does with a PDF
# that has no text is its own policy: the Library refuses (extract_pdf), chat
# documents show the page pictures (pdf_pages + page_pictures).
import io
import logging
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_PICTURES_PER_PAGE = 8
PICTURE_MAX_SIDE = 2000


def decode_bytes(raw: bytes) -> str:
    """utf-8 (a BOM dropped) first, cp1252 second (the Win10 default), replace as
    last resort. Latin-1 is never tried: it can't fail, so anything binary would
    "decode" and get embedded as junk."""
    for enc in ('utf-8-sig', 'cp1252'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def is_binary(raw: bytes) -> bool:
    """A NUL in the head: no 8-bit text file has one. Catches docx, zip, exe, images."""
    return b'\x00' in raw[:8192]


def decode_text(raw: bytes):
    """(text, error) for a file that claims to be text. Newlines normalized: CRLF
    never matches the chunker's paragraph split, and the whole file becomes one chunk."""
    # UTF-16 is full of NULs but really is text: Notepad's "Unicode", PowerShell's `>`
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        try:
            text = raw.decode('utf-16')
        except UnicodeDecodeError:
            return None, "That file claims to be UTF-16 but isn't readable."
    elif is_binary(raw):
        return None, "That looks like a binary file, not text."
    else:
        text = decode_bytes(raw)
    return text.replace('\r\n', '\n').replace('\r', '\n'), None


def _open_pdf(raw):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw))
    if reader.is_encrypted:
        reader.decrypt('')      # an empty password opens the merely restricted ones
    return reader


def pdf_pages(raw):
    """(page texts, file_meta, error). A scanned page is an empty string here, not
    a refusal. A wrong-password PDF is: reading its pages raises."""
    try:
        reader = _open_pdf(raw)
    except Exception as e:
        if 'password' in str(e).lower() or 'decrypt' in str(e).lower():
            return None, {}, "PDF is password-protected, can't read it."
        return None, {}, f"PDF parse failed: {e}"
    try:
        pages = [(p.extract_text() or '').strip() for p in reader.pages]
    except Exception as e:
        if 'password' in str(e).lower() or 'decrypt' in str(e).lower():
            return None, {}, "PDF is password-protected, can't read it."
        return None, {}, f"PDF parse failed: {e}"
    meta = {}
    try:
        info = reader.metadata
        if info and info.title:
            meta['title'] = str(info.title).strip()
        if info and info.author:
            meta['author'] = str(info.author).strip()
    except Exception:
        pass
    return pages, meta, None


def has_text_layer(pages) -> bool:
    """False for a scan: nothing, or a stray page number on each page."""
    text = '\n\n'.join(p for p in pages if p)
    return bool(text) and len(text) >= 40 * max(1, len(pages))


def extract_pdf(raw):
    """(text, file_meta, error) with a scanned PDF refused: for callers that need
    text and have no way to show a page."""
    pages, meta, err = pdf_pages(raw)
    if err:
        return None, {}, err
    if not has_text_layer(pages):
        return None, {}, ("This PDF has no usable text layer (scanned pages?), "
                          "OCR isn't supported. Export it as text and import that instead.")
    return '\n\n'.join(p for p in pages if p), meta, None


def page_pictures(raw, number):
    """(pictures, why): one PDF page's embedded images as [(jpeg bytes, 'image/jpeg')],
    number counted from 1. pypdf reads images out of a page, it doesn't draw one:
    a vector chart has none to give, and a JBIG2 scan needs the external jbig2dec.
    `why` says so whenever something the page holds can't be shown."""
    from PIL import Image
    try:
        reader = _open_pdf(raw)
        if not 1 <= number <= len(reader.pages):
            return [], f"There is no page {number}, this PDF has {len(reader.pages)}."
        found = reader.pages[number - 1].images
        names = list(found.keys())
    except Exception as e:
        return [], f"Couldn't read page {number}: {e}"
    if not names:
        return [], (f"Page {number} holds no pictures. Any drawings on it are vector "
                    "graphics, which can't be shown.")
    out, failed = [], None
    for name in names[:MAX_PICTURES_PER_PAGE]:
        try:
            img = found[name].image
            img.thumbnail((PICTURE_MAX_SIDE, PICTURE_MAX_SIDE), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.convert('RGB').save(buf, 'JPEG', quality=85)
            out.append((buf.getvalue(), 'image/jpeg'))
        except Exception as e:
            logger.warning(f"[Extract] page {number} picture {name}: {e}")
            failed = e
    why = None
    if failed is not None:
        how = "needs jbig2dec, which isn't installed" if 'jbig2' in str(failed).lower() else str(failed)
        why = f"Some pictures on page {number} couldn't be read ({how})."
    elif len(names) > MAX_PICTURES_PER_PAGE:
        why = f"Page {number} has {len(names)} pictures, showing the first {MAX_PICTURES_PER_PAGE}."
    return out, why


class _HTMLText(HTMLParser):
    """EPUB chapter HTML -> plain text + first heading (stdlib, no bs4). Heading text
    is captured separately and re-emitted as a markdown # so a chunker's chapter
    breadcrumbs work."""
    def __init__(self):
        super().__init__()
        self.parts, self.title = [], None
        self._skip = 0
        self._in_head = False
        self._head = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self._skip += 1
        elif tag in ('h1', 'h2', 'h3') and self.title is None:
            self._in_head = True
        elif tag in ('p', 'div', 'br', 'li', 'tr'):
            self.parts.append('\n\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self._skip = max(0, self._skip - 1)
        elif tag in ('h1', 'h2', 'h3') and self._in_head:
            self._in_head = False
            t = ' '.join(''.join(self._head).split())
            if t and self.title is None:
                self.title = t

    def handle_data(self, data):
        if self._skip:
            return
        (self._head if self._in_head else self.parts).append(data)


def html_to_text(html):
    p = _HTMLText()
    try:
        p.feed(html)
    except Exception:
        pass
    text = re.sub(r'\n{3,}', '\n\n',
                  '\n'.join(l.strip() for l in ''.join(p.parts).split('\n')))
    return text.strip(), p.title


def extract_epub(raw):
    """(text, file_meta, error). ebooklib needs a real path, so it's staged through a
    temp file. Each spine document becomes a '# chapter' section."""
    try:
        import ebooklib
        from ebooklib import epub
        # delete=False + close before reopen: Windows cannot open a
        # NamedTemporaryFile by name while the first handle is live.
        tf = tempfile.NamedTemporaryFile(suffix='.epub', delete=False)
        try:
            tf.write(raw)
            tf.close()
            book = epub.read_epub(tf.name, options={'ignore_ncx': True})
        finally:
            try:
                Path(tf.name).unlink(missing_ok=True)
            except OSError:
                pass
        parts = []
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            text, head = html_to_text(item.get_content().decode('utf-8', 'replace'))
            if not text:
                continue
            parts.append(f"# {head}\n\n{text}" if head else text)
        text = '\n\n'.join(parts)
        if not text.strip():
            return None, {}, ("EPUB contained no readable text, likely DRM-protected. "
                              "Import a DRM-free copy.")
        meta = {}
        try:
            t = book.get_metadata('DC', 'title')
            a = book.get_metadata('DC', 'creator')
            if t:
                meta['title'] = str(t[0][0]).strip()
            if a:
                meta['author'] = str(a[0][0]).strip()
        except Exception:
            pass
        return text, meta, None
    except Exception as e:
        return None, {}, f"EPUB parse failed (DRM or corrupt?): {e}"
