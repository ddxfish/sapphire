"""core/worker_door.py: pinned TLS + per-client keys, over a real loopback socket."""
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from core import worker_door as door


class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        who = door.client_of(self.server.door, self.headers.get("Authorization"))
        body = json.dumps({"client": who}).encode()
        self.send_response(200 if who else 401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def worker(tmp_path):
    srv = door.serve(ThreadingHTTPServer, Echo, "127.0.0.1", 0, tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield tmp_path, f"127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_a_paired_client_gets_in_as_itself(worker):
    folder, address = worker
    door.add_client(folder, "VM Sapphire")
    s, base = door.session(door.pairing(folder, "vm-sapphire", address))
    r = s.get(f"{base}/health", timeout=5)
    assert r.status_code == 200 and r.json()["client"] == "vm-sapphire"


def test_a_wrong_or_removed_key_is_refused(worker):
    folder, address = worker
    door.add_client(folder, "vm")
    text = door.pairing(folder, "vm", address)
    base, _, fp = door.parse(text)
    s, _ = door.session(text.replace("key=", "key=x"))
    assert s.get(f"{base}/", timeout=5).status_code == 401
    door.remove_client(folder, "vm")
    s, _ = door.session(text)
    assert s.get(f"{base}/", timeout=5).status_code == 401


def test_a_worker_with_another_cert_is_never_trusted(worker, tmp_path_factory):
    folder, address = worker
    door.add_client(folder, "vm")
    other = tmp_path_factory.mktemp("other")
    text = door.pairing(folder, "vm", address).replace(door.pin(folder), door.pin(other))
    with pytest.raises(ssl.SSLError):
        door.session(text)


def test_plain_http_and_unpinned_tls_get_nothing(worker):
    folder, address = worker
    with pytest.raises(requests.exceptions.ConnectionError):
        requests.get(f"http://{address}/", timeout=5)
    with pytest.raises(requests.exceptions.SSLError):
        requests.get(f"https://{address}/", timeout=5)          # a stranger's normal TLS: the cert is nobody's CA


def test_parse_refuses_what_is_not_a_pairing():
    for bad in ("", "http://h:1/#key=a&pin=" + "0" * 64, "https://h:1/#key=a&pin=short", "https://h:1/#pin=" + "0" * 64):
        with pytest.raises(ValueError):
            door.parse(bad)
    assert door.parse("https://h:1/#key=a&pin=" + "A" * 64) == ("https://h:1", "a", "a" * 64)


def test_kokoro_reaches_a_paired_kokoro_and_keeps_its_key_out_of_logs(worker, caplog):
    from core.tts.providers import kokoro
    folder, address = worker
    door.add_client(folder, "vm")
    text = door.pairing(folder, "vm", address)
    p = kokoro.KokoroTTSProvider.__new__(kokoro.KokoroTTSProvider)
    p._doors = {}
    assert p._check_health(text, timeout=5) is True
    assert p._check_health(text.replace("key=", "key=x"), timeout=5) is False
    assert kokoro._shown(text) == f"https://{address}/"
    assert "key=" not in kokoro._shown(text) and kokoro._shown("http://localhost:5012") == "http://localhost:5012"


def test_plugin_copies_say_exactly_what_core_says():
    """A plugin whose server also runs alone carries a copy (FlashHead's
    worker_door.py): a drifted copy would speak another protocol."""
    from pathlib import Path
    core = Path(door.__file__).read_bytes()
    root = Path(door.__file__).parent.parent
    for copy in [*root.glob("user/plugins/*/worker_door.py"), *root.glob("core/plugins/*/worker_door.py")]:
        assert copy.read_bytes() == core, f"{copy} differs from core/worker_door.py: copy it again"


def test_the_door_says_who_came_and_who_was_refused(tmp_path, caplog):
    class Seen(Echo):
        def do_GET(self):
            who = door.admit(self)
            self.send_response(200 if who else 401)
            self.send_header("Content-Length", "0")
            self.end_headers()
    srv = door.serve(ThreadingHTTPServer, Seen, "127.0.0.1", 0, tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        door.add_client(tmp_path, "vm")
        text = door.pairing(tmp_path, "vm", f"127.0.0.1:{srv.server_address[1]}")
        with caplog.at_level("INFO", logger="worker_door"):
            s, base = door.session(text)
            assert s.get(f"{base}/", timeout=5).status_code == 200
            bad, _ = door.session(text.replace("key=", "key=x"))
            assert bad.get(f"{base}/", timeout=5).status_code == 401
        assert door.seen(tmp_path)["vm"]["from"] == "127.0.0.1"
        assert "vm came in from 127.0.0.1" in caplog.text and "Refused a key from 127.0.0.1" in caplog.text
        door.remove_client(tmp_path, "vm")
        assert "vm" not in door.seen(tmp_path)
    finally:
        srv.shutdown()
