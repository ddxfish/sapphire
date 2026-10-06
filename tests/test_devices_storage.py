"""[REGRESSION_GUARD] Backups onto devices (2026-10-06): the satellite's card
and a folder on this computer as backup targets, driven through the real
drivers against a fake board / a temp folder. Pins:

- SatelliteTarget is remote: a plain tar never goes up, a .sapphirebak must
  carry the magic, the PUT streams the file with X-Sha256 and the board's
  sha is checked (a mismatch deletes the copy and fails).
- A ship to a satellite rotates by its keep fields and drops the openers.
- The Backup tab: describe() offers backup + list; format appears only when
  the board's /health says `storage.can: ["format"]`, owner-only + danger.
- Owner-only actions: engine.run refuses her, the owner gets through, and
  her help screen never lists them.
- FolderTarget (this computer): no folder = no target and no Backup actions;
  a missing folder is reported, never created; inside user/ refused; plain
  by default, sealed when `backup_encrypt` is on.
- core/devices/storage.targets() builds one Target per device that has
  storage, through engine.doors.
"""
import hashlib
import tarfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import backup_crypto
from core.devices.drivers import satellite as sat
from core.devices.drivers import computer as pc

DEV = {'id': 'den', 'label': 'Den', 'location': 'Den'}
CFG = {'url': 'http://192.168.1.50:80', 'camera': False, 'chat': '',
       'keep_daily': 2, 'keep_weekly': 4, 'keep_monthly': 3}


class Secrets(dict):
    def get(self, k, default=''):
        return dict.get(self, k, default)

    def scrub(self, text):
        return text


KEY = Secrets(token='board-key-abcdefgh')


class Reply:
    def __init__(self, data=None, status=200, content=b''):
        self._data, self.status_code, self.content = data, status, content

    def json(self):
        if self._data is None:
            raise ValueError('no json')
        return self._data

    def iter_content(self, n):
        yield self.content


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    """A backup manager over a tiny user/ with a password, wired into backup_targets."""
    import core.backup as B
    import core.backup_targets as T
    from core.backup import Backup
    with patch.object(Backup, '__init__', lambda self: None):
        b = Backup()
    b.base_dir = tmp_path
    b.user_dir = tmp_path / "user"
    b._backup_dir_override = None
    b.backup_dir_error = None
    b.user_dir.mkdir(parents=True)
    (b.user_dir / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "decrypt_backup.py").write_text("# stub\n", encoding="utf-8")
    b._stop_event = None
    b._backup_op_lock = threading.Lock()

    class Cfg:
        BASE_DIR = tmp_path
        BACKUPS_ENABLED = True
        BACKUPS_EXCLUDE_PATTERNS = []
        BACKUPS_KEEP_DAILY = 7
        BACKUPS_KEEP_WEEKLY = 4
        BACKUPS_KEEP_MONTHLY = 3
        BACKUPS_KEEP_MANUAL = 5
        BACKUPS_KEEP_UPDATE = 3
        BACKUPS_HOUR = 3
        BACKUPS_ENCRYPT_LOCAL = False
        BACKUPS_DIR = ""
    monkeypatch.setattr(B, "config", Cfg)
    monkeypatch.setattr(T, "backup_manager", b)
    monkeypatch.setattr(T, "_providers", [])
    monkeypatch.setattr(b, "_backup_password", lambda: "pw")
    monkeypatch.setattr(b, "_alert", lambda kind, **d: None)
    # the drivers reach the manager through core.backup (folder-inside-user check)
    monkeypatch.setattr(B, "backup_manager", b)
    return b


class Board:
    """A fake satellite with a card: the four doors, the magic check (GATE 4),
    sha256 answered, and a /health that may say it can format."""

    def __init__(self, can_format=False, lie_sha=False):
        self.files = {}
        self.calls = []
        self.can = ['format'] if can_format else []
        self.lie_sha = lie_sha
        self.formatted = 0

    def health(self):
        return {"ok": True, "name": "den", "board": "waveshare-s3-audio", "firmware": "0.2.0",
                "has": ["speaker", "mic", "wake", "light", "power", "storage"],
                "storage": {"free_bytes": 12 * 1024 ** 3, "total_bytes": 15 * 1024 ** 3, "can": self.can}}

    def __call__(self, method, url, **kw):
        path = url.replace(CFG['url'], '').split('?')[0]
        self.calls.append(SimpleNamespace(method=method, path=path, kw=kw))
        if path == '/health':
            return Reply(self.health())
        if path == '/storage' and method == 'GET':
            return Reply({"ok": True, "free_bytes": 12 * 1024 ** 3, "total_bytes": 15 * 1024 ** 3,
                          "path": "/sdcard/backups", "can": self.can,
                          "files": [{"name": n, "size": len(b), "mtime": 0} for n, b in self.files.items()]})
        if path == '/storage/format' and method == 'POST':
            self.files.clear()
            self.formatted += 1
            return Reply({"ok": True})
        if path.startswith('/storage/'):
            name = path[len('/storage/'):]
            if method == 'PUT':
                body = kw.get('data')
                data = body.read() if hasattr(body, 'read') else bytes(body or b'')
                openers = ('README.txt', 'open-backup.sh', 'open-backup.bat', 'decrypt_backup.py')
                if name not in openers and not (name.endswith('.sapphirebak') and data[:12] == backup_crypto.MAGIC):
                    return Reply({"detail": "not a sealed backup"}, status=400)
                self.files[name] = data
                sha = hashlib.sha256(data).hexdigest()
                return Reply({"ok": True, "name": name, "size": len(data),
                              "sha256": ('0' * 64) if self.lie_sha else sha})
            if method == 'GET':
                if name not in self.files:
                    return Reply({"detail": "no such file"}, status=404)
                return Reply(content=self.files[name])
            if method == 'DELETE':
                self.files.pop(name, None)
                return Reply({"ok": True})
        return Reply({"ok": True})


@pytest.fixture
def board():
    b = Board()
    sat._about.clear()
    with patch.object(sat.net, 'request', b):
        yield b
    sat._about.clear()


def _plain(mgr):
    return mgr.create_backup("daily")


# --- the satellite target ----------------------------------------------------

def test_satellite_refuses_plaintext_before_any_call(mgr, board):
    from core.backup import BackupRefused
    t = sat.storage_target(DEV, CFG, KEY)
    assert t.remote is True and t.wants_encryption is True
    fn = _plain(mgr)
    with pytest.raises(BackupRefused, match="plaintext"):
        t.put(mgr.backup_dir / fn, fn)
    assert not [c for c in board.calls if c.method == 'PUT'], "nothing reached the board"


def test_ship_to_satellite_streams_sealed_with_sha(mgr, board):
    from core import backup_targets as T
    t = sat.storage_target(DEV, CFG, KEY)
    fn = _plain(mgr)
    res = T.ship(fn, to=[t])
    assert res[0]["ok"], res
    sealed = fn.removesuffix(".tar.gz") + ".sapphirebak"
    assert sealed in board.files and board.files[sealed][:12] == backup_crypto.MAGIC
    put = next(c for c in board.calls if c.method == 'PUT' and c.path.endswith(sealed))
    assert put.kw['headers']['X-Sha256'] == hashlib.sha256(board.files[sealed]).hexdigest()
    assert put.kw['headers']['Content-Type'] == 'application/octet-stream'
    assert put.kw['timeout'] == sat.STORE_WAIT
    assert {"README.txt", "open-backup.sh", "open-backup.bat", "decrypt_backup.py"} <= set(board.files)
    assert not list(mgr.backup_dir.glob("ship_*"))


def test_board_sha_mismatch_fails_and_drops_the_copy(mgr):
    b = Board(lie_sha=True)
    sat._about.clear()
    with patch.object(sat.net, 'request', b):
        t = sat.storage_target(DEV, CFG, KEY)
        sealed, cleanup = mgr.sealed(_plain(mgr))
        with pytest.raises(sat.Problem, match="sha256"):
            t.put(sealed, sealed.name)
        cleanup()
    assert sealed.name not in b.files
    assert any(c.method == 'DELETE' for c in b.calls)


def test_satellite_rotation_uses_its_keep_fields(mgr, board):
    from core import backup_targets as T
    t = sat.storage_target(DEV, CFG, KEY)      # keep_daily = 2
    assert t.keep == {'daily': 2, 'weekly': 4, 'monthly': 3, 'manual': 3}
    for d in ("01", "02", "03"):
        board.files[f"sapphire_2026-10-{d}_030000_daily.sapphirebak"] = backup_crypto.MAGIC + b"old"
    board.files["holiday.jpg"] = b"mine"
    res = T.ship(_plain(mgr), to=[t])
    assert res[0]["ok"], res
    dailies = sorted(n for n in board.files if "_daily." in n)
    assert len(dailies) == 2 and "sapphire_2026-10-01_030000_daily.sapphirebak" not in board.files
    assert "holiday.jpg" in board.files


def test_get_streams_a_backup_back(mgr, board, tmp_path):
    t = sat.storage_target(DEV, CFG, KEY)
    board.files["sapphire_2026-10-05_030000_daily.sapphirebak"] = backup_crypto.MAGIC + b"payload"
    dst = tmp_path / "back.sapphirebak"
    t.get("sapphire_2026-10-05_030000_daily.sapphirebak", dst)
    assert dst.read_bytes() == backup_crypto.MAGIC + b"payload"
    with pytest.raises(sat.Problem):
        t.get("../etc/passwd", dst)


# --- the Backup tab ----------------------------------------------------------

def test_describe_offers_backup_and_list_and_format_only_when_the_board_can(board):
    told = sat.describe(DEV, CFG)
    assert told['storage']['label'] == 'Backup'
    assert set(told['storage']['actions']) == {'backup', 'list'}
    board.can = ['format']
    sat.status(DEV, CFG, KEY)                   # /health lands in the cache
    told = sat.describe(DEV, CFG)
    fmt = told['storage']['actions']['format']
    assert fmt['owner'] is True and 'erased' in fmt['danger']


def test_status_reads_the_card(board):
    st = sat.status(DEV, CFG, KEY)
    assert 'storage' in st['has']
    assert st['readings']['card'] == '12.0 GB free of 15.0 GB'


def test_run_list_and_backup_and_format(mgr, board):
    text, ok = sat.run(DEV, 'storage', 'list', '', CFG, KEY, None)
    assert ok and '0 backup(s) on den' in text and 'free 12.0 GB of 15.0 GB' in text
    text, ok = sat.run(DEV, 'storage', 'backup', '', CFG, KEY, None)
    assert ok and 'Backing up' in text, text               # answers at once, ships on a thread
    from core.devices import storage as st
    for _ in range(100):
        if any(n.endswith('_manual.sapphirebak') for n in board.files) and not st._inflight:
            break
        time.sleep(0.05)
    assert any(n.endswith('_manual.sapphirebak') for n in board.files)
    text, ok = sat.run(DEV, 'storage', 'list', '', CFG, KEY, None)
    assert ok and 'last backup ok' in text, text
    text, ok = sat.run(DEV, 'storage', 'format', '', CFG, KEY, None)
    assert not ok and 'cannot format' in text
    board.can = ['format']
    sat.status(DEV, CFG, KEY)
    text, ok = sat.run(DEV, 'storage', 'format', '', CFG, KEY, None)
    assert ok and board.formatted == 1 and board.files == {}


def test_old_program_without_the_door_is_said(mgr):
    def old(method, url, **kw):
        if '/storage' in url:
            return Reply({"detail": "not found"}, status=404)
        return Reply({"ok": True})
    with patch.object(sat.net, 'request', old):
        text, ok = sat.run(DEV, 'storage', 'list', '', CFG, KEY, None)
    assert not ok and 'no backup door' in text


# --- owner-only actions in the engine ----------------------------------------

def test_engine_refuses_owner_only_actions_for_her():
    from core.devices import engine
    row = {'id': 'den', 'label': 'Den', 'location': '', 'enabled': True, 'locked': [],
           'parts': [{'driver': 'satellite', 'plugin': 'core', 'config': dict(CFG),
                      'has': ['speaker', 'storage']}]}
    fake_mod = SimpleNamespace(
        describe=lambda d, c: {'storage': {'label': 'Backup', 'actions': {
            'list': {'help': 'what is there', 'example': ''},
            'format': {'help': 'wipe', 'example': '', 'owner': True, 'danger': 'Erases everything.'}}}},
        run=lambda *a, **k: ('ran', True))
    spec = {'capabilities': ['speaker', 'storage'], 'plugin_name': 'core', 'uses_tools': ()}
    with patch.object(engine, '_usable', lambda d: row), \
         patch.object(engine, '_driver', lambda *a, **k: (fake_mod, spec)), \
         patch.object(engine, '_part_secrets', lambda *a: Secrets()), \
         patch.object(engine, '_health', lambda: SimpleNamespace(view=lambda i: {'ts': 0, 'online': None, 'parts': []})):
        caps = engine.describe(row)
        st = next(c for c in caps if c['capability'] == 'storage')
        assert st['actions']['format']['owner'] is True and st['actions']['format']['danger'] == 'Erases everything.'
        assert st['lockable'] is True
        text, ok = engine.run('den', 'storage', 'format')
        assert not ok and 'only' in text
        text, ok = engine.run('den', 'storage', 'format', owner=True)
        assert ok and text == 'ran'
        text, ok = engine.run('den', 'storage', 'list')
        assert ok
        screen, ok = engine.run('den', 'storage')
        assert ok and 'format' not in screen and 'list' in screen


# --- a folder on this computer -----------------------------------------------

def test_no_folder_means_no_target_and_no_actions(mgr):
    assert pc.storage_target({'id': 'pc'}, {}, Secrets()) is None
    assert 'storage' not in pc.describe({'id': 'pc'}, {})
    assert 'storage' in pc.describe({'id': 'pc'}, {'backup_path': '/x'})


def test_missing_folder_is_reported_never_created(mgr, tmp_path):
    cfg = {'backup_path': str(tmp_path / 'usb' / 'sapphire')}
    with pytest.raises(pc.Problem, match="not there"):
        pc.storage_target({'id': 'pc'}, cfg, Secrets())
    assert not (tmp_path / 'usb').exists()
    text, ok = pc.run({'id': 'pc'}, 'storage', 'list', '', cfg, Secrets(), None)
    assert not ok and 'plugged in' in text


def test_folder_inside_user_refused(mgr):
    cfg = {'backup_path': str(mgr.user_dir / 'bk')}
    (mgr.user_dir / 'bk').mkdir()
    with pytest.raises(pc.Problem, match="user/"):
        pc.storage_target({'id': 'pc'}, cfg, Secrets())


def test_folder_plain_by_default_sealed_on_switch(mgr, tmp_path):
    from core import backup_targets as T
    usb = tmp_path / 'usb'
    usb.mkdir()
    t = pc.storage_target({'id': 'pc'}, {'backup_path': str(usb)}, Secrets())
    assert t.remote is False and t.wants_encryption is False
    fn = _plain(mgr)
    res = T.ship(fn, to=[t])
    assert res[0]['ok'], res
    with tarfile.open(usb / fn) as tf:
        assert 'user/notes.txt' in tf.getnames()
    assert not list(usb.glob('*.partial'))
    assert {"README.txt", "open-backup.sh", "open-backup.bat", "decrypt_backup.py"} <= {p.name for p in usb.iterdir()}
    t2 = pc.storage_target({'id': 'pc'}, {'backup_path': str(usb), 'backup_encrypt': True}, Secrets())
    assert t2.wants_encryption is True
    fn2 = mgr.create_backup("manual")
    res = T.ship(fn2, to=[t2])
    assert res[0]['ok'], res
    assert backup_crypto.is_encrypted_backup(usb / (fn2.removesuffix('.tar.gz') + '.sapphirebak'))
    text, ok = pc.run({'id': 'pc'}, 'storage', 'list', '', {'backup_path': str(usb)}, Secrets(), None)
    assert ok and '2 backup(s)' in text and str(usb) in text


# --- the bridge --------------------------------------------------------------

def test_storage_targets_come_from_engine_doors(mgr, board, tmp_path):
    from core.devices import storage as st
    from core.devices import engine
    usb = tmp_path / 'usb'
    usb.mkdir()
    doors = [(sat, DEV, dict(CFG), KEY),
             (pc, {'id': 'pc', 'label': 'PC', 'location': ''}, {'backup_path': str(usb)}, Secrets()),
             (pc, {'id': 'pc2', 'label': 'PC2', 'location': ''}, {}, Secrets())]
    with patch.object(engine, 'doors', lambda cap, hook, online=True: doors):
        ts = st.targets()
        assert [t.kind for t in ts] == ['satellite', 'folder']
        assert st.target_for('den').kind == 'satellite'
        assert st.target_for('nobody') is None


# --- a network mount is another machine (2026-10-06) ------------------------------

def test_network_mount_makes_the_folder_remote(tmp_path):
    from unittest.mock import mock_open
    from core.backup import BackupRefused
    cfg = {'backup_path': str(tmp_path), 'backup_encrypt': False}
    plain = tmp_path / 'sapphire_2026-10-06_030000_daily.tar.gz'
    plain.write_bytes(b'x' * 10)
    with patch.object(pc, 'is_network_mount', return_value=False):
        t = pc.FolderTarget({'id': 'den'}, cfg)
        assert not t.remote and not t.wants_encryption
        t.check(plain, plain.name)                      # plain allowed on a local folder
    with patch.object(pc, 'is_network_mount', return_value=True):
        t = pc.FolderTarget({'id': 'den'}, cfg)
        assert t.remote and t.wants_encryption           # the checkbox cannot say otherwise
        with pytest.raises(BackupRefused):
            t.check(plain, plain.name)


@pytest.mark.skipif(not hasattr(pc, 'os') or pc.os.name == 'nt', reason='/proc/mounts')
def test_is_network_mount_reads_proc_mounts(tmp_path):
    from unittest.mock import mock_open
    mounts = ("/dev/sda1 / ext4 rw 0 0\n"
              "//nas/backups /mnt/nas cifs rw,vers=3.0 0 0\n"
              "nas:/export /mnt/nfs\\040share nfs4 rw 0 0\n")
    with patch('builtins.open', mock_open(read_data=mounts)):
        assert pc.is_network_mount('/mnt/nas/sapphire') is True
        assert pc.is_network_mount('/mnt/nas') is True
        assert pc.is_network_mount('/mnt/nfs share/x') is True
        assert pc.is_network_mount('/mnt/nasty') is False          # prefix, not a mount
        assert pc.is_network_mount('/home/me/backups') is False
