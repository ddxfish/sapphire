"""core/http_words.py — what the screen shows for a route's failure: words,
never str(e); a 422 as one sentence, never a list ("[object Object]")."""
import re
from pathlib import Path

from fastapi.exceptions import RequestValidationError

from core.http_words import server_error, invalid_request


def test_a_500_says_what_failed_first_and_its_reason_last():
    e = server_error("install the plugin", OSError('No space left on device'))
    assert e.status_code == 500
    assert e.detail.startswith("Sapphire couldn't install the plugin. Try again.")
    assert e.detail.endswith('(No space left on device)') and 'OSError' not in e.detail


def test_a_reason_can_stay_in_the_log():
    e = server_error("load settings", KeyError('secret'), why=False)
    assert 'secret' not in e.detail


def test_422_reads_as_one_sentence():
    words = invalid_request([{'loc': ('body', 'enabled'), 'msg': 'Input should be a valid boolean'},
                             {'loc': ('query', 'chat'), 'msg': 'Field required'}])
    assert isinstance(words, str)
    assert 'enabled: Input should be a valid boolean' in words and 'chat: Field required' in words


def test_the_app_answers_422_in_words():
    from core.api_fastapi import app
    assert RequestValidationError in app.exception_handlers


def test_no_route_sends_a_raw_exception_on_a_500():
    leaks = []
    for p in Path('core/routes').glob('*.py'):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if re.search(r'status_code=500,\s*detail=(str\(e\)|f".*\{e\})', line):
                leaks.append(f"{p}:{n}")
    assert not leaks, leaks


def test_a_path_in_the_reason_never_names_the_person():
    from core.http_words import _HOME
    e = server_error("reload that plugin", OSError(f"[Errno 13] Permission denied: '{_HOME}/sapphire/user/plugins/x'"))
    assert _HOME not in e.detail and "'~/sapphire/user/plugins/x'" in e.detail
