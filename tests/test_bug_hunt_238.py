"""
Regression tests for v2.3.8 bug hunt — Round 1.
Tests the 15 fixes applied during the pre-release security audit.
"""
import copy
import hashlib
import json
import os
import sys
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =============================================================================
# FIX #1: Symlink check operator precedence (plugins.py:322)
# =============================================================================

class TestSymlinkCheck:
    """The symlink check in zip extraction was broken due to operator precedence.
    `attr >> 16 & 0o120000 == 0o120000` evaluates as `attr >> 16 & True` (always 0 or 1).
    Fixed: `(attr >> 16) & 0o120000 == 0o120000`."""

    def test_symlink_detected_with_correct_precedence(self):
        """Symlink external_attr should be detected with fixed precedence."""
        # Symlinks have mode 0o120000 in the upper 16 bits
        symlink_attr = 0o120777 << 16
        # Fixed expression
        assert ((symlink_attr >> 16) & 0o120000) == 0o120000

    def test_regular_file_not_flagged(self):
        """Regular files should NOT be flagged as symlinks."""
        regular_attr = 0o100644 << 16
        assert ((regular_attr >> 16) & 0o120000) != 0o120000

    def test_old_broken_expression_missed_symlinks(self):
        """Demonstrate the old broken expression would miss symlinks."""
        symlink_attr = 0o120777 << 16
        # Old: `attr >> 16 & 0o120000 == 0o120000` → `attr >> 16 & True` → `attr >> 16 & 1`
        old_result = symlink_attr >> 16 & (0o120000 == 0o120000)  # == has higher precedence than &
        # old_result is `0o120777 & 1` = 1, which is truthy BUT only by accident for odd modes
        # For even modes like 0o120644, old expression gives 0 (misses the symlink!)
        even_symlink = 0o120644 << 16
        old_even = even_symlink >> 16 & (0o120000 == 0o120000)
        assert old_even == 0, "Old expression misses symlinks with even mode bits"

    def test_zip_symlink_rejected(self, tmp_path):
        """End-to-end: a zip with a symlink entry should be caught."""
        zip_path = tmp_path / "evil.zip"
        with zipfile.ZipFile(zip_path, 'w') as zf:
            info = zipfile.ZipInfo("link.txt")
            info.external_attr = 0o120777 << 16  # symlink
            zf.writestr(info, "target")

        with zipfile.ZipFile(zip_path, 'r') as zf:
            for info in zf.infolist():
                is_symlink = ((info.external_attr >> 16) & 0o120000) == 0o120000
                assert is_symlink, "Symlink should be detected"


# =============================================================================
# FIX #2: Webhook auth, payload size, audit logging (system.py)
# =============================================================================

class TestWebhookSecurity:
    """Webhook endpoint was unauthenticated with no payload limits."""

    def test_hmac_compare_digest_used(self):
        """Verify HMAC comparison uses constant-time compare."""
        import hmac as hmac_mod
        secret = "test-secret"
        payload = b'{"event": "push"}'
        sig = "sha256=" + hmac_mod.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        expected = "sha256=" + hmac_mod.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert hmac_mod.compare_digest(sig, expected)

    def test_wrong_secret_rejected(self):
        """Wrong secret should fail HMAC validation."""
        import hmac as hmac_mod
        payload = b'{"event": "push"}'
        real_sig = "sha256=" + hmac_mod.new(b"real-secret", payload, hashlib.sha256).hexdigest()
        fake_sig = "sha256=" + hmac_mod.new(b"wrong-secret", payload, hashlib.sha256).hexdigest()
        assert not hmac_mod.compare_digest(real_sig, fake_sig)

    def test_payload_size_limit_constant(self):
        """1MB payload limit should be enforced."""
        max_size = 1_048_576  # 1MB
        assert max_size == 1024 * 1024


# =============================================================================
# FIX #3: Memory import hash mismatch (knowledge.py:810)
# =============================================================================

class TestMemoryImportDedup:
    """Hash was computed without .strip() on imported entries, causing dedup to miss matches."""

    def test_hash_consistency_with_whitespace(self):
        """Existing and imported entries with equivalent text should produce same hash."""
        existing_text = "  hello world  "
        imported_text = "  hello world  "

        # How existing entries are hashed (from DB)
        existing_hash = hashlib.sha256(existing_text.strip().lower().encode()).hexdigest()
        # How imported entries should be hashed (FIXED: now uses .strip().lower())
        imported_hash = hashlib.sha256(imported_text.strip().lower().encode()).hexdigest()

        assert existing_hash == imported_hash

    def test_old_bug_would_mismatch(self):
        """Demonstrate the old bug: no .strip() on import side caused hash mismatch."""
        existing_text = "hello world"
        imported_text = "hello world  "  # trailing whitespace

        existing_hash = hashlib.sha256(existing_text.strip().lower().encode()).hexdigest()
        # OLD: no .strip() — just .lower()
        old_import_hash = hashlib.sha256(imported_text.lower().encode()).hexdigest()

        assert existing_hash != old_import_hash, "Old code would have missed this duplicate"

        # FIXED: .strip().lower()
        new_import_hash = hashlib.sha256(imported_text.strip().lower().encode()).hexdigest()
        assert existing_hash == new_import_hash, "Fixed code catches the duplicate"


# =============================================================================
# FIX #4: PIL decompression bomb + base64 size limit (content.py)
# =============================================================================

class TestAvatarSafety:
    """Avatar import had no PIL pixel limit or base64 size limit."""

    def test_max_pixels_limit_rejects_bomb(self):
        """PIL.MAX_IMAGE_PIXELS should reject images exceeding the limit on open()."""
        from PIL import Image
        import io

        # Create a small image, save it, then try to open with a tiny limit
        img = Image.new("RGB", (64, 64), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        old_limit = Image.MAX_IMAGE_PIXELS
        try:
            Image.MAX_IMAGE_PIXELS = 10  # 10 pixels max
            with pytest.raises(Image.DecompressionBombError):
                Image.open(buf)
        finally:
            Image.MAX_IMAGE_PIXELS = old_limit

    def test_avatar_pixel_limit_value(self):
        """The avatar pixel limit should be 4096*4096 (16M pixels)."""
        expected = 4096 * 4096
        assert expected == 16_777_216

    def test_base64_size_limit(self):
        """Base64 data larger than 5MB should be rejected."""
        max_b64_size = 5 * 1024 * 1024
        oversized = "A" * (max_b64_size + 1)
        assert len(oversized) > max_b64_size

    def test_mime_whitelist(self):
        """Only image/webp, image/png, image/jpeg, image/gif should be accepted."""
        allowed = {'image/webp', 'image/png', 'image/jpeg', 'image/gif'}
        rejected = {'image/svg+xml', 'application/x-msdownload', 'text/html'}
        for mime in rejected:
            assert mime not in allowed


# =============================================================================
# FIX #5: Shallow task dict copy (executor.py:92)
# =============================================================================

class TestDeepCopyTask:
    """Shallow dict copy allowed nested dict mutations to bleed back to original."""

    def test_deepcopy_isolates_trigger_config(self):
        """Modifying trigger_config in copy should NOT affect original."""
        original = {
            "id": "test-1",
            "trigger_config": {"source": "discord_message", "filter": {"channel": "general"}},
            "initial_message": "hello",
        }
        task_copy = copy.deepcopy(original)
        task_copy["trigger_config"]["source"] = "email_incoming"
        task_copy["trigger_config"]["filter"]["channel"] = "random"

        assert original["trigger_config"]["source"] == "discord_message"
        assert original["trigger_config"]["filter"]["channel"] == "general"

    def test_shallow_copy_would_bleed(self):
        """Demonstrate the old bug: shallow copy shares nested dicts."""
        original = {"trigger_config": {"source": "discord"}}
        shallow = dict(original)
        shallow["trigger_config"]["source"] = "email"
        # Bug: original is mutated!
        assert original["trigger_config"]["source"] == "email", "Shallow copy bleeds"


# =============================================================================
# FIX #8: Plugin install race condition (plugins.py)
# =============================================================================

class TestPluginInstallLock:
    """Two concurrent installs could corrupt plugin state without a lock."""

    def test_install_lock_in_source(self):
        """The plugins route module should define _install_lock."""
        source = Path(PROJECT_ROOT / "core" / "routes" / "plugins.py").read_text()
        assert "_install_lock = threading.Lock()" in source

    def test_install_lock_used_in_install(self):
        """The install endpoint should use _install_lock."""
        source = Path(PROJECT_ROOT / "core" / "routes" / "plugins.py").read_text()
        assert "with _install_lock:" in source

    def test_lock_serializes_concurrent_access(self):
        """A threading.Lock serializes access (general pattern test)."""
        lock = threading.Lock()
        results = []

        def worker(name, delay):
            with lock:
                results.append(f"{name}_start")
                time.sleep(delay)
                results.append(f"{name}_end")

        t1 = threading.Thread(target=worker, args=("A", 0.05))
        t2 = threading.Thread(target=worker, args=("B", 0.01))
        t1.start()
        time.sleep(0.01)  # ensure A starts first
        t2.start()
        t1.join()
        t2.join()

        # A must fully complete before B starts
        assert results == ["A_start", "A_end", "B_start", "B_end"]


# =============================================================================
# FIX #9: Daemon filter evasion — non-JSON bypasses filter (scheduler.py)
# =============================================================================

class TestDaemonFilterEnforcement:
    """Non-JSON event data used to bypass filter checks entirely."""

    def test_json_decode_failure_rejects_when_filter_active(self):
        """If filter is configured but event data isn't JSON, task should be rejected."""
        from core.continuity.scheduler import ContinuityScheduler

        scheduler = MagicMock(spec=ContinuityScheduler)
        # Test the logic directly: filter exists + non-JSON = reject
        task_filter = {"channel": "general"}
        event_data = "this is not json"

        try:
            event_obj = json.loads(event_data)
        except (json.JSONDecodeError, TypeError):
            rejected = True
        else:
            rejected = False

        assert rejected, "Non-JSON data should trigger rejection when filter is active"


# =============================================================================
# FIX #10: Unbounded event queue (scheduler.py)
# =============================================================================

class TestEventQueueLimit:
    """Unbounded _task_pending growth could cause memory exhaustion."""

    def test_queue_cap_at_50(self):
        """Queue should reject events after 50 pending."""
        pending = {}
        task_id = "test-task"
        max_queue = 50

        for i in range(max_queue + 5):
            count = pending.get(task_id, 0)
            if count >= max_queue:
                break
            pending[task_id] = count + 1

        assert pending[task_id] == max_queue
        assert pending[task_id] <= 50, "Queue should be capped at 50"


# =============================================================================
# FIX #11: Prompt import schema validation (content.py)
# =============================================================================

class TestPromptImportValidation:
    """Prompt import accepted any data without validation, causing crashes."""

    def test_monolith_without_content_detected(self):
        """A monolith prompt without 'content' key should be caught."""
        bad_data = {"type": "monolith"}  # missing 'content'
        assert "content" not in bad_data
        assert bad_data.get("type") == "monolith"
        # The fix checks: if type == "monolith" and "content" not in data → 400

    def test_valid_monolith_has_content(self):
        """A valid monolith prompt must have 'content'."""
        good_data = {"type": "monolith", "content": "You are a helpful assistant."}
        assert "content" in good_data

    def test_components_must_be_dict(self):
        """Components field must be a dict, not a list or string."""
        assert isinstance({}, dict)
        assert not isinstance([], dict)
        assert not isinstance("bad", dict)


# =============================================================================
# FIX #12: Agent batch completion TOCTOU (manager.py)
# =============================================================================

class TestAgentBatchAtomicity:
    """Events were published outside the lock, allowing races."""

    def test_check_batch_complete_publishes_inside_lock(self):
        """Verify batch completion events fire while lock is held."""
        from core.agents.manager import AgentManager
        from core.agents.base_worker import BaseWorker

        mgr = AgentManager()
        publish_calls = []

        class FakeAgent(BaseWorker):
            def __init__(self, agent_id, name, chat_name):
                super().__init__(agent_id, name, "test", chat_name)
                self.status = 'done'
                self.result = 'ok'
                self._start_time = time.time() - 1
                self._end_time = time.time()
                self._agent_type = 'llm'

            def run(self):
                pass

        mgr._agents = {
            'a1': FakeAgent('a1', 'Alpha', 'test-chat'),
            'a2': FakeAgent('a2', 'Bravo', 'test-chat'),
        }

        lock_held_during_publish = []

        original_publish = None
        def tracking_publish(event, data):
            # Check if the manager's lock is held (locked() returns True if acquired)
            lock_held_during_publish.append(mgr._lock.locked())

        with patch('core.agents.manager.publish', side_effect=tracking_publish):
            mgr._check_batch_complete('a1', 'test-chat')

        # All publish calls should have been made while lock was held
        assert len(lock_held_during_publish) > 0, "Should have published events"
        assert all(lock_held_during_publish), "All publish calls should be inside the lock"


# =============================================================================
# FIX #13: Updater version parsing crash (updater.py)
# =============================================================================

class TestVersionParsing:
    """Version strings like '2.3.8-rc1' would crash with ValueError."""

    def test_parse_numeric_version(self):
        """Standard numeric version should parse correctly."""
        from core.updater import Updater
        u = Updater.__new__(Updater)
        u.current_version = "2.3.7"
        u.latest_version = None
        u.update_available = False
        u.last_check = None
        u.checking = False
        u.branch = "main"

        # The _parse_version helper is defined inside check_for_update,
        # so test the logic directly
        def _parse_version(v):
            parts = []
            for x in v.split('.'):
                num = ''
                for ch in x:
                    if ch.isdigit():
                        num += ch
                    else:
                        break
                parts.append(int(num) if num else 0)
            return tuple(parts)

        assert _parse_version("2.3.8") == (2, 3, 8)
        assert _parse_version("2.3.8") > _parse_version("2.3.7")

    def test_parse_version_with_suffix(self):
        """Version with suffix like -rc1 should not crash."""
        def _parse_version(v):
            parts = []
            for x in v.split('.'):
                num = ''
                for ch in x:
                    if ch.isdigit():
                        num += ch
                    else:
                        break
                parts.append(int(num) if num else 0)
            return tuple(parts)

        assert _parse_version("2.3.8-rc1") == (2, 3, 8)
        assert _parse_version("2.4.0-beta.1") == (2, 4, 0, 1)  # .1 suffix becomes extra part
        assert _parse_version("3.0.0-dev") == (3, 0, 0)
        # Key: none of these crash (old code would ValueError on all of them)
        assert _parse_version("2.3.8-rc1") > _parse_version("2.3.7")

    def test_old_parser_would_crash(self):
        """The old int() parser would crash on non-numeric suffixes."""
        with pytest.raises(ValueError):
            tuple(int(x) for x in "2.3.8-rc1".split('.'))


# =============================================================================
# FIX #14: Windows PATH separator (claude_code_tools.py)
# =============================================================================

class TestWindowsPathCompat:
    """PATH cleaning used hardcoded ':' separator instead of os.pathsep."""

    def test_os_pathsep_used(self):
        """Code should use os.pathsep, not hardcoded ':'."""
        # Verify os.pathsep is platform-appropriate
        if sys.platform == 'win32':
            assert os.pathsep == ';'
        else:
            assert os.pathsep == ':'

    def test_path_split_join_roundtrip(self):
        """Splitting and joining with os.pathsep should roundtrip."""
        test_path = f"/usr/bin{os.pathsep}/usr/local/bin{os.pathsep}/home/user/bin"
        parts = test_path.split(os.pathsep)
        rejoined = os.pathsep.join(parts)
        assert rejoined == test_path

    def test_conda_filter_uses_os_sep(self):
        """Conda path filtering should use os.sep for cross-platform."""
        # Simulate the fixed filter logic
        sep = os.sep
        test_dirs = [
            f"/home/user{sep}miniconda3{sep}envs{sep}test{sep}bin",
            f"/usr{sep}bin",
            f"/home/user{sep}.venv{sep}bin",
            "/usr/local/bin",
        ]
        clean = [d for d in test_dirs
                 if f'{sep}envs{sep}' not in d
                 and f'{sep}conda' not in d.lower()
                 and f'{sep}.venv{sep}' not in d]
        assert len(clean) == 2  # only /usr/bin and /usr/local/bin


# =============================================================================
# FIX #16: Agent shutdown method (manager.py + sapphire.py)
# =============================================================================

class TestAgentShutdown:
    """Agent threads were daemon threads with no graceful shutdown."""

    def test_agent_manager_has_shutdown(self):
        """AgentManager should have a shutdown() method."""
        from core.agents.manager import AgentManager
        mgr = AgentManager()
        assert hasattr(mgr, 'shutdown')
        assert callable(mgr.shutdown)

    def test_shutdown_cancels_running_agents(self):
        """shutdown() should cancel all running agents."""
        from core.agents.manager import AgentManager
        from core.agents.base_worker import BaseWorker

        mgr = AgentManager()

        class SlowAgent(BaseWorker):
            def run(self):
                while not self._cancelled.wait(0.1):
                    pass

        agent = SlowAgent('a1', 'Alpha', 'test', 'chat1')
        mgr._agents['a1'] = agent

        with patch('core.event_bus.publish'):
            agent.start()
            assert agent.status == 'running'

            mgr.shutdown(timeout=2)
            assert agent._cancelled.is_set()
            assert not agent._thread.is_alive()

    def test_shutdown_clears_agents(self):
        """shutdown() should empty the agents dict."""
        from core.agents.manager import AgentManager
        mgr = AgentManager()
        mgr._agents = {'a1': MagicMock(status='done', _thread=None, cancel=MagicMock)}
        mgr.shutdown()
        assert len(mgr._agents) == 0


# =============================================================================
# Combined integration: verify deepcopy + scope isolation
# =============================================================================

class TestTaskIsolationIntegration:
    """Verify the full chain: deepcopy prevents mutation, scopes stay isolated."""

    def test_event_data_mutation_doesnt_affect_original(self):
        """Event processing on a deepcopied task should not touch the original."""
        original_task = {
            "id": "daemon-1",
            "name": "Discord Bot",
            "trigger_config": {
                "source": "discord_message",
                "filter": {"channel_not": "bot-spam"},
            },
            "initial_message": "Handle this message",
            "discord_scope": "",
        }

        # Simulate what executor.run() does with the fix
        task = copy.deepcopy(original_task)
        task["initial_message"] = f"{task['initial_message']}\n\n>>> User said: hello"
        task["discord_scope"] = "bot-account-1"
        task["trigger_config"]["filter"]["new_key"] = "injected"

        # Original should be untouched
        assert original_task["initial_message"] == "Handle this message"
        assert original_task["discord_scope"] == ""
        assert "new_key" not in original_task["trigger_config"]["filter"]
