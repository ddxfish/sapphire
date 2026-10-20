"""/api/notifications (core/routes/notifications.py) through the app's test
client: list and filters, post, dedupe by key, read one, read all, dismiss
(409 while active), clear. The store is the hermetic tmp one from conftest."""
import pytest


@pytest.fixture
def api(client):
    from core import notify
    c, csrf = client
    return c, {'X-CSRF-Token': csrf}, notify


def test_round_trip(api):
    c, h, n = api
    r = c.post('/api/notifications', json={'title': 'hi', 'level': 'warning', 'body': 'b',
                                           'go': {'view': 'settings', 'tab': 'tts'}, 'key': 'k'}, headers=h)
    assert r.status_code == 200, r.text
    rid = r.json()['id']
    assert r.json()['row']['go'] == {'view': 'settings', 'tab': 'tts'} and r.json()['counts']['yellow'] == 1
    assert c.get('/api/notifications').json()['rows'][0]['id'] == rid
    assert c.get('/api/notifications?level=error').json()['rows'] == []
    assert c.get('/api/notifications?level=error,warning&unread=true').json()['rows'][0]['id'] == rid
    assert c.post(f'/api/notifications/{rid}/read', headers=h).json()['counts']['yellow'] == 0
    assert c.get('/api/notifications?unread=true').json()['rows'] == []
    again = c.post('/api/notifications', json={'title': 'again', 'level': 'warning', 'key': 'k'}, headers=h).json()
    assert again['id'] == rid and again['row']['title'] == 'again' and again['counts']['yellow'] == 1
    assert c.post('/api/notifications/read', headers=h).json()['read'] == 1
    assert c.delete(f'/api/notifications/{rid}', headers=h).status_code == 200
    assert c.delete(f'/api/notifications/{rid}', headers=h).status_code == 404
    assert c.post('/api/notifications/nope/read', headers=h).status_code == 404


def test_bad_posts(api):
    c, h, n = api
    assert c.post('/api/notifications', json={'level': 'error'}, headers=h).status_code == 400
    assert c.post('/api/notifications', json=['no'], headers=h).status_code == 400
    assert c.post('/api/notifications', json={'title': '  '}, headers=h).status_code == 400


def test_active_refuses_dismiss_and_clear_keeps_it(api):
    c, h, n = api
    act = n.begin('dl')
    n.notify('x')
    n.notify('y', 'error')
    assert c.delete(f'/api/notifications/{act}', headers=h).status_code == 409
    assert c.get('/api/notifications').json()['counts'] == {'red': 1, 'yellow': 0, 'active': 1}
    assert c.delete('/api/notifications', headers=h).json()['cleared'] == 2
    assert [r['id'] for r in c.get('/api/notifications').json()['rows']] == [act]
