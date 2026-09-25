"""One password minimum, everywhere (2026-09-24).

A fresh install's setup page listed "Minimum 6 characters" while the same page,
the backend, the change-password form and the docs all enforced 10. Every site
that states or enforces the minimum must agree with core/setup.py.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def _backend_minimum():
    m = re.search(r"len\(password\) < (\d+)", _read('core/setup.py'))
    assert m, 'core/setup.py no longer checks len(password)'
    return int(m.group(1))


def test_every_site_states_the_backend_minimum():
    n = _backend_minimum()
    setup = _read('interfaces/web/templates/setup.html')
    assert f'Minimum {n} characters' in setup
    assert setup.count(f'minlength="{n}"') == 2
    assert f'at least {n} characters' in setup and f'password.length < {n}' in setup
    assert f'len(password) < {n}' in _read('core/api_fastapi.py')
    assert f'at least {n} characters' in _read('core/routes/system.py')
    sysjs = _read('interfaces/web/static/views/settings-tabs/system.js')
    assert f'({n}+ characters)' in sysjs and f'minlength="{n}"' in sysjs and f'length < {n}' in sysjs
    assert f'({n}+ characters)' in _read('docs/INSTALLATION.md')
    assert f'{n}+ chars' in _read('docs/API.md')


def test_no_other_minimum_is_stated():
    n = _backend_minimum()
    for rel in ('interfaces/web/templates/setup.html',
                'interfaces/web/static/views/settings-tabs/system.js'):
        for m in re.finditer(r'(?i)(minimum|at least|minlength=")\s*(\d+)', _read(rel)):
            assert int(m.group(2)) == n, f'{rel}: {m.group(0)!r} disagrees with backend minimum {n}'
