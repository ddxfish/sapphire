# core/plugins/browser/reader.py - the AI snapshot, made readable
#
# Playwright's AI snapshot is a YAML tree where every row, cell and wrapper
# repeats its children's words as its own name: Hacker News' front page is
# 58 K characters of it, 4 K of actual text. This keeps the words once and a
# ref only on what can be used: [a link](e12), [button: Go](e3),
# [textbox: name = Krem](e4). Blocks (rows, paragraphs, list items) become
# lines, headings stay headings. About a tenth of the tree, every ref kept.
import re

_LINE = re.compile(r'^(?P<ind>\s*)- (?:text: (?P<text>.*)|(?P<role>[a-zA-Z]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?'
                   r'(?P<attrs>(?: \[[^\]]*\])*)(?P<colon>:)?(?: (?P<inline>.*))?)$')
USABLE = {'link', 'button', 'textbox', 'searchbox', 'checkbox', 'radio', 'combobox', 'menuitem', 'menuitemcheckbox',
          'menuitemradio', 'tab', 'switch', 'slider', 'spinbutton', 'option', 'treeitem'}
BLOCK = {'row', 'listitem', 'paragraph', 'article', 'section', 'region', 'navigation', 'main', 'banner',
         'contentinfo', 'form', 'dialog', 'alertdialog', 'table', 'list', 'blockquote', 'complementary', 'figure',
         'term', 'definition', 'tablist', 'menu', 'listbox', 'radiogroup', 'tree', 'alert', 'status', 'search'}
QUIET = {'generic', 'separator', 'group', 'rowgroup'}         # wrappers whose name is only their children's
STATES = ('checked', 'selected', 'disabled', 'expanded', 'pressed')


def _unquote(s):
    s = (s or '').strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        s = s[1:-1]
    return s.replace('\\"', '"')


def read(snap):
    lines, cur = [], []
    skip = None                       # depth whose children are dropped (they repeat their parent)
    head = None                       # depth of a heading whose children make its line

    def flush():
        if cur:
            lines.append(' '.join(cur))
            cur.clear()

    for raw in snap.splitlines():
        m = _LINE.match(raw)
        if not m or raw.lstrip().startswith('- /'):
            continue                                  # /url: and other props
        depth = len(m['ind'])
        if head is not None and depth <= head:
            flush()                                   # the heading's line ends with its children
            head = None
        if skip is not None:
            if depth > skip:
                continue
            skip = None
        if m['text'] is not None:
            if t := _unquote(m['text']):
                cur.append(t)
            continue
        role, attrs = m['role'], m['attrs'] or ''
        name, inline = _unquote(m['name']), _unquote(m['inline'])
        ref = re.search(r'\[ref=((?:f\d+)?e\d+)\]', attrs)     # f1e5 = inside an iframe
        states = [a for a in re.findall(r'\[([^\]]+)\]', attrs) if a.split('=')[0] in STATES]
        if role in USABLE and ref:
            label = f"{name} = {inline}" if name and inline else (name or inline)
            tag = '' if role == 'link' else role + ': '
            cur.append(f"[{tag}{label}{' (' + ', '.join(states) + ')' if states else ''}]({ref.group(1)})")
            if m['colon'] and role in ('link', 'button'):
                skip = depth
            continue
        if role == 'option':                          # a select's options carry no ref: name them, mark the chosen
            cur.append(name + (' (selected)' if 'selected' in states else ''))
            continue
        if role == 'heading':
            flush()
            hashes = '#' * int((re.search(r'level=(\d)', attrs) or [0, 2])[1])
            if m['colon']:
                # its children repeat its name, but a link inside it is the
                # title she wants to click: "## [Post](e6)", not a bare "## Post"
                cur.append(hashes)
                head = depth
            else:
                lines.append(f"{hashes} {name or inline}")
            continue
        if role == 'img':
            if name:
                cur.append(f'[image: {name}]')
            continue
        if role in BLOCK and head is None:
            flush()
        if inline:
            cur.append(inline)
        elif name and not m['colon'] and role not in QUIET:
            cur.append(name)
        if role in BLOCK and head is None and (inline or not m['colon']):   # a block with no children ends here
            flush()
    flush()
    return '\n'.join(l for l in lines if l.strip())
