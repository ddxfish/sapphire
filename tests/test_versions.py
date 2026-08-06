"""core/versions.py — the one version parser (2026-08-06 updater hunt, M1).

Replaced three rival parsers whose failure semantics disagreed:
- updater.py: garbage → (0,) tuples (unreadable local VERSION advertised updates)
- routes/plugins.py: any parse failure → (0,) (v-prefixed remote NEVER offered)
- routes/store.py: strict ints, None on failure
Contract now: lenient parse, conservative compare — unparseable never
advertises an update.
"""
from core.versions import parse_version, is_newer


class TestParseVersion:
    def test_plain_semver(self):
        assert parse_version('1.2.3') == (1, 2, 3)

    def test_v_prefix(self):
        # The plugins-route parser returned (0,) for this — a v-prefixed
        # remote release was never offered, forever.
        assert parse_version('v1.2.3') == (1, 2, 3)
        assert parse_version('V2.0') == (2, 0)

    def test_prerelease_suffix(self):
        assert parse_version('2.3.8-rc1') == (2, 3, 8)
        assert parse_version('1.0.0-beta') == (1, 0, 0)

    def test_part_without_digits_reads_zero(self):
        assert parse_version('1.x.3') == (1, 0, 3)

    def test_garbage_is_none(self):
        assert parse_version('banana') is None
        assert parse_version('?') is None
        assert parse_version('') is None
        assert parse_version(None) is None

    def test_whitespace_tolerated(self):
        assert parse_version(' 1.2.3\n') == (1, 2, 3)

    def test_v_alone_is_none(self):
        assert parse_version('v') is None


class TestIsNewer:
    def test_basic_newer(self):
        assert is_newer('1.17.0', '1.16.0') is True
        assert is_newer('1.16.0', '1.17.0') is False
        assert is_newer('1.16.0', '1.16.0') is False

    def test_zero_padding_equal(self):
        # Old updater parser called '1.2.0' newer than '1.2'.
        assert is_newer('1.2.0', '1.2') is False
        assert is_newer('1.2', '1.2.0') is False
        assert is_newer('1.2.1', '1.2') is True

    def test_unparseable_never_advertises(self):
        # Unreadable local VERSION ('?') used to parse as (0,) and make ANY
        # remote look like an update. Conservative now: no update.
        assert is_newer('2.10.3', '?') is False
        assert is_newer('garbage', '1.0.0') is False
        assert is_newer(None, '1.0.0') is False
        assert is_newer('1.0.0', None) is False

    def test_v_prefix_compares(self):
        assert is_newer('v1.17.0', '1.16.0') is True

    def test_multipart(self):
        assert is_newer('1.10.0', '1.9.9') is True
