"""Every ?error= code the login and setup routes send has its own words on
the page. A missing one read "An error occurred" (a locked-out user never
learned it was the rate limit)."""
import re
from pathlib import Path

SRC = Path('core/api_fastapi.py').read_text()
TPL = Path('interfaces/web/templates')


def _codes(page):
    return set(re.findall(rf'url="/{page}\?error=(\w+)"', SRC))


def test_every_login_error_has_words():
    html = (TPL / 'login.html').read_text()
    assert _codes('login') and all(f"'{c}':" in html for c in _codes('login')), _codes('login')


def test_every_setup_error_has_words():
    html = (TPL / 'setup.html').read_text()
    assert _codes('setup') and all(f"'{c}':" in html for c in _codes('setup')), _codes('setup')
