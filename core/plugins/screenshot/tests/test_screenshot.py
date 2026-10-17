"""Simple Screenshot — logic tests (no real capture; backends are mocked)."""
import base64
import io
from unittest.mock import patch

from plugins.screenshot.tools import screenshot as ss


def test_source_user_falls_back_to_paste_on_timeout():
    # No browser responds → _browser_capture returns None → paste guidance.
    with patch.object(ss, "_browser_capture", return_value=None):
        out, ok = ss.execute("get_screenshot", {"source": "user"}, {})
    assert ok
    assert "paste" in out.lower() or "upload" in out.lower()


def test_source_user_returns_browser_image():
    img = ({"text": "The user shared their screen.",
            "images": [{"data": "ZmFrZQ==", "media_type": "image/png"}]}, True)
    with patch.object(ss, "_browser_capture", return_value=img):
        out, ok = ss.execute("get_screenshot", {"source": "user"}, {})
    assert ok and isinstance(out, dict)
    assert out["images"][0]["media_type"] == "image/png"


def test_source_user_browser_error_passthrough():
    with patch.object(ss, "_browser_capture", return_value=("Screen share declined.", False)):
        out, ok = ss.execute("get_screenshot", {"source": "user"}, {})
    assert not ok and out == "Screen share declined."


def test_unknown_function_rejected():
    out, ok = ss.execute("nope", {}, {})
    assert not ok


def test_encode_png_downscales_to_cap_and_stays_valid_png():
    from PIL import Image
    big = Image.new("RGB", (4000, 1200), (10, 20, 30))
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    b64 = ss._encode_png(buf.getvalue())
    raw = base64.b64decode(b64)
    assert raw[:8] == ss._PNG_MAGIC
    out = Image.open(io.BytesIO(raw))
    assert max(out.size) <= ss._MAX_DIM_DEFAULT


def test_local_no_display_env_routes_to_portal():
    # The service often starts with no WAYLAND_DISPLAY/DISPLAY — must still try
    # the D-Bus portal rather than dying in mss.
    env_no_display = {k: v for k, v in ss.os.environ.items()
                      if k not in ("WAYLAND_DISPLAY", "DISPLAY")}
    with patch.object(ss.platform, "system", return_value="Linux"), \
         patch.dict(ss.os.environ, env_no_display, clear=True), \
         patch.object(ss, "_capture_portal", return_value=b"PNGBYTES") as portal, \
         patch.object(ss, "_capture_mss", side_effect=AssertionError("mss must not run")), \
         patch.object(ss, "_encode_png", return_value="ENCODED"):
        b64, reason = ss._capture_local()
    assert portal.called
    assert b64 == "ENCODED" and reason is None


def test_local_falls_back_when_no_wayland_backend():
    with patch.object(ss.platform, "system", return_value="Linux"), \
         patch.dict(ss.os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=False), \
         patch.object(ss.shutil, "which", return_value=None):
        b64, reason = ss._capture_local()
    assert b64 is None and reason == "FALLBACK"


def test_execute_local_fallback_message_points_to_paste():
    with patch.object(ss, "_capture_local", return_value=(None, "FALLBACK")):
        out, ok = ss.execute("get_screenshot", {"source": "local"}, {})
    assert ok and "paste" in out.lower()


def test_execute_mss_missing_message():
    with patch.object(ss, "_capture_local", return_value=(None, "MSS_MISSING")):
        out, ok = ss.execute("get_screenshot", {"source": "local"}, {})
    assert ok and "mss" in out.lower()


def test_execute_local_success_returns_image_contract():
    with patch.object(ss, "_capture_local", return_value=("ZmFrZQ==", None)):
        out, ok = ss.execute("get_screenshot", {}, {})  # default source=local
    assert ok and isinstance(out, dict)
    assert out["images"][0]["data"] == "ZmFrZQ=="
    assert out["images"][0]["media_type"] == "image/png"


def test_png_complete_rejects_truncated_and_empty(tmp_path):
    # The portal creates the file then writes async — a 0-byte or truncated
    # (no IEND) read is the bug we guard against. Only a full PNG passes.
    from PIL import Image
    full = tmp_path / "full.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(full, format="PNG")
    assert ss._png_complete(full)

    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    assert not ss._png_complete(empty)

    truncated = tmp_path / "trunc.png"
    truncated.write_bytes(full.read_bytes()[:-8])  # strip the IEND chunk
    assert not ss._png_complete(truncated)


def test_capture_portal_none_without_gdbus():
    with patch.object(ss.shutil, "which", return_value=None):
        assert ss._capture_portal() is None


def test_browser_capture_handshake_end_to_end():
    """tool _browser_capture ↔ route handle_capture share state via sys.modules:
    tool blocks → route sees pending nonce → frame delivered → tool returns it."""
    import base64
    import threading
    import time
    from plugins.screenshot.routes import capture as route

    assert route.get_pending() == {"pending": False}

    res = {}
    t = threading.Thread(target=lambda: res.update(out=ss._browser_capture()))
    t.start()
    try:
        # wait for the tool to register its pending slot
        for _ in range(50):
            p = route.get_pending()
            if p.get("pending"):
                break
            time.sleep(0.05)
        assert p["pending"] and p["nonce"]

        png = base64.b64encode(ss._PNG_MAGIC + b"x" * 200).decode()
        assert route.handle_capture(
            {"nonce": p["nonce"], "data": png, "media_type": "image/png"}
        ) == {"status": "ok"}
    finally:
        t.join(timeout=5)

    out, ok = res["out"]
    assert ok and out["images"][0]["media_type"] == "image/png"
    assert route.get_pending() == {"pending": False}  # slot cleared after delivery
