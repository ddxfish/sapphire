"""Source tripwires on web/index.js (the house suite has no JS runtime for plugins)."""

import re
from pathlib import Path

_JS = (Path(__file__).absolute().parents[1] / 'web' / 'index.js').read_text(encoding='utf-8')


def test_esc_escapes_quotes_for_attribute_context():
    # H1 (hunt 2026-09-12): esc() fed data-content="…" but left quotes alone —
    # a Discord user's fact broke out of the attribute with the owner's session.
    body = re.search(r'function esc\(str\) \{(.*?)\n\}', _JS, re.DOTALL).group(1)
    assert '&quot;' in body
    assert '&#39;' in body


def test_attribute_sites_still_go_through_esc():
    assert 'data-content="${esc(f.content)}"' in _JS
