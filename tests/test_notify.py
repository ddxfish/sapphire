"""Notification center engine (core/notify.py): rows, the ring, dedupe keys,
progress rows, read/dismiss/clear, the boot sweep and the routing of the old
bus events. Run with: pytest tests/test_notify.py -v"""
import json

import pytest


@pytest.fixture
def bus(monkeypatch):
    """A fresh singleton bus; notify's events are ephemeral, so they are read
    back through a hook, not the replay ring."""
    import core.event_bus as eb
    fresh = eb.EventBus(replay_size=10)
    monkeypatch.setattr(eb, '_bus', fresh)
    return fresh


@pytest.fixture
def seen(bus):
    out = []
    bus.on(['notification', 'notification_progress', 'notification_flash', 'notification_read',
            'notifications_read', 'notification_gone', 'notifications_cleared'], out.append)
    return out


@pytest.fixture
def n(tmp_path, monkeypatch, bus):
    from core import notify
    monkeypatch.setattr(notify, 'STORE', tmp_path / 'notifications.json')
    monkeypatch.setattr(notify, '_rows', {})
    monkeypatch.setattr(notify, '_last_tick', {})
    monkeypatch.setattr(notify, '_loaded', False)
    monkeypatch.setattr(notify, '_hooked', False)
    monkeypatch.setattr(notify, '_boot_results', lambda: None)   # the dev box has real result files
    return notify


def _saved(n):
    return json.loads(n.STORE.read_text(encoding='utf-8'))


class TestRows:
    def test_notify_writes_a_row_and_publishes_it(self, n, seen):
        rid = n.notify('Hello', 'warning', 'body', source='piper', go={'view': 'settings', 'tab': 'tts'})
        row = n.get(rid)
        assert row['level'] == 'warning' and row['title'] == 'Hello' and row['body'] == 'body'
        assert row['go'] == {'view': 'settings', 'tab': 'tts'} and row['read'] is False
        assert [r['id'] for r in _saved(n)] == [rid]
        assert seen[-1]['type'] == 'notification' and seen[-1]['data']['id'] == rid
        assert seen[-1]['data']['origin'] is None

    def test_bad_level_is_info_and_junk_go_is_none(self, n):
        rid = n.notify('x', 'loud', go={'view': 42})
        assert n.get(rid)['level'] == 'info' and n.get(rid)['go'] is None
        assert n.get(n.notify('x', go={'hash': '#store/plugins/x'}))['go'] == {'hash': '#store/plugins/x'}
        assert n.get(n.notify('x', go={'hash': 'store'}))['go'] is None

    def test_key_refires_one_row(self, n, seen):
        a = n.notify('first', 'error', key='plugin-load:x')
        n.mark_read(a)
        b = n.notify('second', 'error', key='plugin-load:x')
        assert a == b and len(n.rows()) == 1
        row = n.get(a)
        assert row['title'] == 'second' and row['read'] is False

    def test_private_is_a_flash_not_a_row(self, n, seen):
        assert n.notify('secret', 'error', 'words', private=True) is None
        assert n.rows() == []
        assert seen[-1]['type'] == 'notification_flash' and seen[-1]['data']['message'] == 'secret: words'

    def test_ring_prunes_oldest_but_never_active(self, n, monkeypatch):
        monkeypatch.setattr(n, 'RING', 4)
        active = n.begin('download')
        ids = [n.notify(f'n{i}') for i in range(6)]
        left = [r['id'] for r in n.rows()]
        assert active in left and len(left) == 4
        assert ids[-3:] == sorted(ids[-3:], key=lambda i: left.index(i), reverse=False) or set(ids[-3:]) <= set(left)

    def test_rows_active_first_then_newest(self, n):
        old = n.notify('old')
        act = n.begin('busy')
        new = n.notify('new')
        assert [r['id'] for r in n.rows()] == [act, new, old]

    def test_rows_filters(self, n):
        n.notify('a', 'error', source='x')
        n.notify('b', 'warning', source='y')
        n.notify('c', 'info', source='x')
        assert {r['title'] for r in n.rows(level='error,warning')} == {'a', 'b'}
        assert {r['title'] for r in n.rows(source='x')} == {'a', 'c'}
        n.mark_all_read()
        assert n.rows(unread=True) == []


class TestCounts:
    def test_counts_and_read(self, n):
        e = n.notify('e', 'error')
        n.notify('c', 'critical')
        n.notify('w', 'warning')
        n.notify('s', 'success')
        n.notify('i', 'info')
        n.begin('dl')
        assert n.counts() == {'red': 2, 'yellow': 1, 'active': 1}
        n.mark_read(e)
        assert n.counts()['red'] == 1
        assert n.mark_all_read() >= 3
        assert n.counts() == {'red': 0, 'yellow': 0, 'active': 1}

    def test_dismiss_and_clear_respect_active(self, n, seen):
        act = n.begin('dl')
        rid = n.notify('x', 'error')
        with pytest.raises(ValueError):
            n.dismiss(act)
        assert n.dismiss(rid) is True and n.dismiss(rid) is False
        assert seen[-1]['type'] == 'notification_gone'
        n.notify('y')
        n.notify('z')
        assert n.clear() == 2
        assert [r['id'] for r in n.rows()] == [act]


class TestProgress:
    def test_context_manager_done(self, n, seen):
        with n.progress('Downloading', source='piper') as p:
            rid = p.id
            assert n.get(rid)['state'] == 'active' and n.get(rid)['progress']['indeterminate'] is True
            p.update(50, 200)
        row = n.get(rid)
        assert row['state'] == 'done' and row['level'] == 'success' and row['progress']['percent'] == 100
        ticks = [e for e in seen if e['type'] == 'notification_progress']
        assert ticks and ticks[0]['data']['progress']['percent'] == 25
        assert _saved(n)[0]['state'] == 'done'

    def test_context_manager_failed_reraises(self, n):
        with pytest.raises(RuntimeError):
            with n.progress('Downloading') as p:
                rid = p.id
                raise RuntimeError('no route to host')
        row = n.get(rid)
        assert row['state'] == 'failed' and row['level'] == 'error' and row['body'] == 'no route to host'
        assert n.counts()['red'] == 1

    def test_failed_words_come_from_the_producer_when_asked(self, n):
        with pytest.raises(RuntimeError):
            with n.progress('dl', words=lambda e: 'offline mode is on') as p:
                rid = p.id
                raise RuntimeError('check your Internet connection')
        assert n.get(rid)['body'] == 'offline mode is on'

    def test_tick_on_a_finished_row_is_ignored(self, n, seen):
        rid = n.begin('x')
        n.finish(rid)
        before = len(seen)
        n.tick(rid, percent=10)
        assert len(seen) == before

    def test_boot_sweep_marks_interrupted(self, n):
        rid = n.begin('x')
        n._loaded = False
        n._rows.clear()
        n._load()
        row = n.get(rid)
        assert row['state'] == 'failed' and row['body'] == 'Interrupted by a restart.'


class TestRouting:
    def test_init_hooks_the_old_events(self, n, bus, seen):
        n.init()
        bus.publish('plugin_load_error', {'plugin': 'piper', 'error': 'no onnx', 'missing_deps': ['onnx'], 'hint': 'pip it'})
        rows = n.rows()
        assert rows[0]['level'] == 'warning' and rows[0]['key'] == 'plugin-load:piper'
        assert 'pip it' in rows[0]['body'] and rows[0]['go'] == {'view': 'settings', 'tab': 'plugins'}
        bus.publish('plugin_notice', {'plugin': 'piper', 'message': 'Voice ready', 'severity': 'success'})
        assert len(n.rows()) == 1 and seen[-1]['type'] == 'notification_flash'
        bus.publish('plugin_notice', {'plugin': 'piper', 'message': 'Voice download failed', 'severity': 'error'})
        assert n.rows()[0]['title'] == 'Voice download failed' and n.rows()[0]['source'] == 'piper'
        bus.publish('sapphire_health_alert', {'type': 'db_integrity', 'dbs': ['memory.db']})
        assert n.rows()[0]['level'] == 'critical' and 'memory.db' in n.rows()[0]['title']
        bus.publish('continuity_task_error', {'task': 'morning', 'error': 'boom'})
        assert n.rows()[0]['go'] == {'view': 'scheduled'} and n.rows()[0]['key'] == 'task:morning'

    def test_init_is_idempotent(self, n, bus):
        n.init()
        n.init()
        bus.publish('plugin_reloaded', {'plugin': 'clock'})
        assert len(n.rows()) == 1 and n.rows()[0]['level'] == 'success'


class TestProgressMore:
    def test_lazy_row_only_on_first_tick(self, n):
        with n.progress('x', lazy=True) as p:
            assert p.id is None and n.rows() == []
        assert n.rows() == []                      # never ticked: no row at all
        with n.progress('y', lazy=True, done_title='Y ready') as p:
            p.update(1, 2)
            rid = p.id
        assert n.get(rid)['title'] == 'Y ready' and n.get(rid)['state'] == 'done'

    def test_failed_title(self, n):
        with pytest.raises(ValueError):
            with n.progress('dl', failed_title='dl failed') as p:
                rid = p.id
                raise ValueError('nope')
        assert n.get(rid)['title'] == 'dl failed' and n.get(rid)['body'] == 'nope'

    def test_tqdm_reporter_counts_bytes(self, n, seen, monkeypatch):
        monkeypatch.setattr(n, 'TICK_S', 0)
        with n.progress('hf') as p:
            T = n.tqdm_for(p)
            assert T is not None
            # the hub's byte bar, with the kwargs it really passes
            with T(unit='B', unit_scale=True, total=1000, initial=0, desc='f', disable=None,
                   name='huggingface_hub.http_get') as bar:
                bar.update(250)
                bar.update(250.0)
            # snapshot_download's file-count bar: no unit, just an iterable
            assert list(T(iter([1, 2]), total=2, desc='files')) == [1, 2]
            rid = p.id
        ticks = [e for e in seen if e['type'] == 'notification_progress']
        assert [t['data']['progress']['percent'] for t in ticks] == [25, 50]
        assert n.get(rid)['state'] == 'done' and n.get(rid)['progress']['percent'] == 100

    def test_reembed_hook_makes_one_row(self, n, monkeypatch):
        import core.embeddings.reembed as re_
        monkeypatch.setattr(re_, '_nid', None)
        base = {'total': 0, 'done': 0, 'current_table': None, 'errors': 0, 'last_error': None}
        re_._tell(dict(base, running=True))
        assert n.rows()[0]['state'] == 'active' and n.counts()['active'] == 1
        re_._tell(dict(base, running=True, total=10, done=5, current_table='memories'))
        re_._tell(dict(base, running=False, total=10, done=10, current_table='done', errors=1))
        row = n.rows()[0]
        assert len(n.rows()) == 1 and row['state'] == 'done'
        assert row['title'] == 'Re-embedded 10 entries, 1 errors' and re_._nid is None


class TestLogRows:
    def test_webhook_and_task_done_are_log_rows(self, n, bus):
        n.init()
        bus.publish('webhook_fired', {'path': 'ping', 'method': 'POST', 'task_id': 't1'})
        bus.publish('continuity_task_complete', {'task_id': 't2', 'task_name': 'morning', 'status': 'complete',
                                                 'details': {'response': 'private words'}})
        rows = n.rows()
        assert [r['level'] for r in rows] == ['info', 'info']
        assert rows[0]['title'] == 'Task "morning" complete' and 'private words' not in rows[0]['body']
        assert rows[1]['title'] == 'Webhook fired: /ping'
        assert n.counts() == {'red': 0, 'yellow': 0, 'active': 0}

    def test_prune_drops_the_log_first(self, n, monkeypatch):
        monkeypatch.setattr(n, 'RING', 4)
        e1 = n.notify('e1', 'error')
        n.notify('i1', 'info')
        n.notify('s1', 'success')
        w1 = n.notify('w1', 'warning')
        e2 = n.notify('e2', 'error')          # five rows: the oldest log rows go, errors stay
        n.notify('i2', 'info')
        left = {r['id'] for r in n.rows()}
        assert {e1, w1, e2} <= left and len(left) == 4


class TestWave5:
    def test_once_posts_one_row_ever(self, n):
        a = n.notify('Updated to v2', 'success', key='update-result:1', once=True)
        n.mark_read(a)
        assert n.notify('Updated to v2', 'success', key='update-result:1', once=True) == a
        assert n.get(a)['read'] is True and len(n.rows()) == 1

    def test_finish_carries_the_origin(self, n, seen):
        rid = n.begin('install')
        n.finish(rid, title='installed', origin='tab-1')
        assert seen[-1]['data']['origin'] == 'tab-1' and seen[-1]['data']['title'] == 'installed'

    def test_restart_row_becomes_restarted_at_boot(self, n):
        n.restarting('applying the update')
        dl = n.begin('a download')
        n._loaded = False
        n._rows.clear()
        n._load()
        rows = {r['key']: r for r in n.rows()}
        assert rows['restart']['state'] == 'done' and rows['restart']['title'] == 'Restarted'
        assert rows['restart']['body'] == 'Restarting: applying the update'
        assert n.get(dl)['state'] == 'failed' and n.get(dl)['body'] == 'Interrupted by a restart.'

    def test_boot_results_pass_errors_through_once(self, n, monkeypatch):
        import core.updater as up, core.gpu_install as gi, core.restore as rs
        monkeypatch.setattr(up, 'read_last_update_result', lambda clear=False: {'success': False, 'message': 'git fetch timed out', 'timestamp': 1700000000})
        monkeypatch.setattr(gi, 'last_result', lambda: {'ok': True, 'index': 'cu124', 'message': 'installed', 'at': '2026-10-20 10:00:00'})
        monkeypatch.setattr(rs, 'read_restore_result', lambda clear=False: {'ok': True, 'source': 'backup:sapphire_x.tar.gz', 'ts': '2026-10-20_100000'})
        live = getattr(n, '_boot_results_live', n._boot_results)
        live()
        live()                                              # a second boot with the same files: nothing new
        rows = sorted(n.rows(), key=lambda r: r['title'])
        assert [r['title'] for r in rows] == ['GPU torch installed (cu124)', 'Restored from backup:sapphire_x.tar.gz', 'Update failed']
        assert {r['level'] for r in rows} == {'success', 'error'}
        assert n.get(rows[2]['id'])['body'] == 'git fetch timed out' and n.counts()['red'] == 1

    def test_ship_failed_alert_is_not_a_second_row(self, n, bus):
        n.init()
        bus.publish('sapphire_health_alert', {'type': 'backup_ship_failed', 'target': 'pi', 'reason': 'no card'})
        assert n.rows() == []
        bus.publish('sapphire_health_alert', {'type': 'backup_ship_refused', 'reason': 'no targets'})
        assert n.rows()[0]['level'] == 'error'
