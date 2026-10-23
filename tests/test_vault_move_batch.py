"""Bulk vault move — 2026-09-08 (record: tmp/prompt-vault-bulk-plan.md).

Krem multi-checked ~100 prompts → Move In → the view flashed every ~2s for
five minutes and looked like nothing moved. The client looped
/api/vault/move; every success published a change event the same tab
reacted to with a whole-view reload, throttling the batch to one item per
two seconds, blind. Now one request runs the per-item movers server-side
(same paths, publish off) and publishes ONCE, with per-item receipts.
"""
from unittest.mock import MagicMock

import pytest

from core import prompt_crud


@pytest.fixture
def movers(monkeypatch):
    """Stub the four per-item movers + the publish; record every call."""
    calls, published = [], []
    def mk(name, ok=True):
        def fn(*args, publish=True):
            calls.append((name, args, publish))
            return ok, f"{name} ok" if ok else "boom"
        return fn
    monkeypatch.setattr(prompt_crud, 'move_piece_to_vault', mk('piece_in'))
    monkeypatch.setattr(prompt_crud, 'move_piece_from_vault', mk('piece_out'))
    monkeypatch.setattr(prompt_crud, 'move_prompt_to_vault', mk('prompt_in'))
    monkeypatch.setattr(prompt_crud, 'move_prompt_from_vault', mk('prompt_out'))
    monkeypatch.setattr(prompt_crud, '_publish_vault_changed',
                        lambda components=False: published.append(components))
    import core.prompt_vault as pv
    monkeypatch.setattr(pv, 'vault_unlocked', lambda: True)
    return calls, published


def test_batch_runs_pieces_first_publish_off_then_publishes_once(movers):
    calls, published = movers
    out = prompt_crud.move_batch('in', [
        {'kind': 'prompt', 'name': 'sapph-first'},
        {'kind': 'piece', 'comp_type': 'emotions', 'key': 'happy'},
        {'kind': 'prompt', 'name': 'cobalt'},
    ])
    assert [c[0] for c in calls] == ['piece_in', 'prompt_in', 'prompt_in']
    assert all(c[2] is False for c in calls), "per-item publish must be OFF inside a batch"
    assert published == [True], "exactly one event, components=True because a piece moved"
    assert out['moved'] == 3 and out['failed'] == 0
    assert [r['ok'] for r in out['results']] == [True, True, True]


def test_batch_out_direction_and_no_pieces_publishes_prompts_only(movers):
    calls, published = movers
    out = prompt_crud.move_batch('out', [{'kind': 'prompt', 'name': 'x'}])
    assert [c[0] for c in calls] == ['prompt_out']
    assert published == [False]
    assert out['moved'] == 1


def test_batch_failure_is_a_receipt_not_an_abort(movers, monkeypatch):
    calls, published = movers
    def bad(*a, publish=True):
        return False, "Prompt 'ghost' not found in the regular store"
    monkeypatch.setattr(prompt_crud, 'move_prompt_to_vault', bad)
    out = prompt_crud.move_batch('in', [
        {'kind': 'prompt', 'name': 'ghost'},
        {'kind': 'piece', 'comp_type': 'goals', 'key': 'quest'},
    ])
    assert out['moved'] == 1 and out['failed'] == 1
    bad_row = next(r for r in out['results'] if not r['ok'])
    assert bad_row['item']['name'] == 'ghost' and 'not found' in bad_row['msg']
    assert published == [True]


def test_batch_stops_when_the_vault_seals_mid_way(movers, monkeypatch):
    calls, published = movers
    n = {'i': 0}
    def sealing(*a, publish=True):
        n['i'] += 1
        return (True, 'ok') if n['i'] == 1 else (False, prompt_crud.VAULT_LOCKED_MSG)
    monkeypatch.setattr(prompt_crud, 'move_prompt_to_vault', sealing)
    out = prompt_crud.move_batch('in', [{'kind': 'prompt', 'name': f'p{i}'} for i in range(5)])
    assert out['moved'] == 1 and out['failed'] == 4
    assert n['i'] == 2, "after the seal the loop must stop calling movers"
    assert all(r['msg'] == prompt_crud.VAULT_LOCKED_MSG for r in out['results'][1:])


def test_batch_locked_up_front(movers, monkeypatch):
    calls, published = movers
    import core.prompt_vault as pv
    monkeypatch.setattr(pv, 'vault_unlocked', lambda: False)
    out = prompt_crud.move_batch('in', [{'kind': 'prompt', 'name': 'x'}])
    assert out['locked'] is True and out['moved'] == 0 and calls == [] and published == []


def test_batch_bad_kind_is_a_receipt(movers):
    out = prompt_crud.move_batch('in', [{'kind': 'monolith', 'name': 'x'}])
    assert out['failed'] == 1 and 'kind' in out['results'][0]['msg']


# ─── route ───────────────────────────────────────────────────────────────────

def _post(client, body):
    c, csrf = client
    return c.post('/api/vault/move-batch', json=body, headers={'X-CSRF-Token': csrf})


def test_route_returns_receipts(client, monkeypatch):
    monkeypatch.setattr(prompt_crud, 'move_batch',
                        lambda d, items: {'moved': len(items), 'failed': 0, 'results': []})
    r = _post(client, {'direction': 'in', 'items': [{'kind': 'prompt', 'name': 'a'},
                                                     {'kind': 'piece', 'comp_type': 'x', 'key': 'y'}]})
    assert r.status_code == 200, r.text
    assert r.json()['moved'] == 2


def test_route_validates(client, monkeypatch):
    monkeypatch.setattr(prompt_crud, 'move_batch', MagicMock())
    assert _post(client, {'direction': 'sideways', 'items': [{'kind': 'prompt', 'name': 'a'}]}).status_code == 400
    assert _post(client, {'direction': 'in', 'items': []}).status_code == 400
    assert _post(client, {'direction': 'in', 'items': [{'kind': 'monolith'}]}).status_code == 400
    prompt_crud.move_batch.assert_not_called()


def test_route_409_when_locked(client, monkeypatch):
    monkeypatch.setattr(prompt_crud, 'move_batch',
                        lambda d, items: {'moved': 0, 'failed': 1, 'locked': True, 'results': []})
    r = _post(client, {'direction': 'in', 'items': [{'kind': 'prompt', 'name': 'a'}]})
    assert r.status_code == 409
