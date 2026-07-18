"""[REGRESSION_GUARD] Backup exclude patterns + size estimate (Remembrance Stage 1).

Privacy floor always applies; user fnmatch globs add on top; the estimator sums
uncompressed sizes with a per-folder breakdown, and the preview uses the SAME
exclusion logic as the real tar filter.
"""
from core.backup import _cache_excluded, _is_excluded, _privacy_excluded, backup_manager


def test_cache_floor_models_always_excluded():
    """user/models/ is rebuildable-cache territory by contract (2026-07-18) —
    hardcoded so existing installs get it (settings defaults never reach a
    persisted settings.json)."""
    assert _cache_excluded("models")
    assert _cache_excluded("models/hf/blobs/abc123")
    assert _cache_excluded("models/geonames/cities.npz")
    assert not _cache_excluded("models-custom/x")     # no false prefix match
    assert not _cache_excluded("history/chat.db")
    assert _is_excluded("models/hf/x.onnx", [])       # wired into the shared filter
    assert not _is_excluded("memory/library.db", [])


def test_privacy_floor_always_excluded():
    assert _privacy_excluded("plugin_state/foo.bad-123")
    assert _privacy_excluded("x.tmp")
    assert _privacy_excluded("a/b.tmp.456")
    assert _privacy_excluded("plugins/discord_mcp_key.json")
    assert _privacy_excluded("mcp_client.json")
    assert _privacy_excluded("sapphire-health.token")
    assert not _privacy_excluded("history/sapphire_history.db")


def test_is_excluded_user_globs():
    pats = ["rag/*", "*.log"]
    assert _is_excluded("rag/doc.pdf", pats)
    assert _is_excluded("rag/sub/deep.txt", pats)   # * crosses /
    assert _is_excluded("logs/app.log", pats)
    assert not _is_excluded("history/chat.db", pats)
    assert _is_excluded("x.tmp", [])                # privacy floor wins regardless
    # bare folder name excludes its whole subtree (the piper-voices case)
    assert _is_excluded("piper-voices/en_US.onnx", ["piper-voices"])
    assert _is_excluded("piper-voices", ["piper-voices"])
    assert not _is_excluded("piper-voices-extra/x", ["piper-voices"])  # no false prefix match


def test_estimate_size_with_exclusions(tmp_path, monkeypatch):
    user = tmp_path / "user"
    (user / "history").mkdir(parents=True)
    (user / "rag").mkdir(parents=True)
    (user / "history" / "chat.db").write_bytes(b"x" * 1000)
    (user / "rag" / "big.pdf").write_bytes(b"y" * 5000)
    (user / "notes.tmp").write_bytes(b"z" * 999)    # privacy floor

    monkeypatch.setattr(backup_manager, "user_dir", user)

    r = backup_manager.estimate_size(patterns=[])
    assert r["total_bytes"] == 6000
    assert r["excluded_bytes"] == 999
    # breakdown is by top-level entry (du --max-depth=1), sorted desc
    assert {b["name"]: b["bytes"] for b in r["breakdown"]} == {"rag": 5000, "history": 1000}

    r2 = backup_manager.estimate_size(patterns=["rag/*"])
    assert r2["total_bytes"] == 1000
    assert r2["excluded_bytes"] == 999 + 5000
    assert r2["breakdown"][0]["name"] == "history"


def test_rotation_keep_zero_retains_tier(tmp_path, monkeypatch):
    """keep<=0 = pause the tier (retain existing), NEVER purge it. War-campaign fix D."""
    import core.backup as B
    bdir = tmp_path / "backups"
    bdir.mkdir()
    for ts in ("2026-06-20_010101", "2026-06-21_010101", "2026-06-22_010101"):
        (bdir / f"sapphire_{ts}_daily.tar.gz").write_bytes(b"x")
    monkeypatch.setattr(B.backup_manager, "backup_dir", bdir)

    class Cfg:
        BACKUPS_KEEP_DAILY = 0
        BACKUPS_KEEP_WEEKLY = 4
        BACKUPS_KEEP_MONTHLY = 3
        BACKUPS_KEEP_MANUAL = 5
    monkeypatch.setattr(B, "config", Cfg)

    assert B.backup_manager.rotate_backups() == 0
    assert len(list(bdir.glob("sapphire_*_daily.tar.gz"))) == 3   # retained, not purged


def test_create_backup_refuses_empty(tmp_path, monkeypatch):
    """A too-broad exclude (`*`) → 0 files → refuse, never write an empty 'success'
    that rotation would keep while aging out the good ones. War-campaign fix A."""
    import core.backup as B
    user = tmp_path / "user"
    user.mkdir()
    (user / "settings.json").write_text("{}")
    bdir = tmp_path / "backups"
    bdir.mkdir()
    monkeypatch.setattr(B.backup_manager, "user_dir", user)
    monkeypatch.setattr(B.backup_manager, "backup_dir", bdir)

    class Cfg:
        BACKUPS_EXCLUDE_PATTERNS = ["*"]
    monkeypatch.setattr(B, "config", Cfg)

    assert B.backup_manager.create_backup("manual") is None
    assert list(bdir.glob("sapphire_*")) == []   # nothing written, not even a partial


def test_create_backup_offsite_params(tmp_path, monkeypatch):
    """create_backup(extra_patterns=, dest_dir=) → plain .tar.gz in dest_dir with
    the extra excludes applied. Core NEVER encrypts — local backups sit next to
    the live data; encryption is the Remembrance plugin's job, pre-upload only."""
    import tarfile
    import core.backup as B
    user = tmp_path / "user"
    (user / "keep").mkdir(parents=True)
    (user / "skipme").mkdir(parents=True)
    (user / "keep" / "a.txt").write_text("KEEP")
    (user / "skipme" / "b.txt").write_text("SKIP")
    dest = tmp_path / "offsite"
    monkeypatch.setattr(B.backup_manager, "user_dir", user)

    class Cfg:
        BACKUPS_EXCLUDE_PATTERNS = []
    monkeypatch.setattr(B, "config", Cfg)

    fn = B.backup_manager.create_backup("offsite", extra_patterns=["skipme"], dest_dir=dest)
    assert fn and fn.endswith(".tar.gz")               # always plaintext from core
    with tarfile.open(dest / fn) as t:
        names = t.getnames()
    assert any(n.endswith("keep/a.txt") for n in names)     # kept
    assert not any("skipme" in n for n in names)            # extra-excluded


def test_backup_filter_skips_symlinks():
    """Symlinks/hardlinks are dropped from backups — path leak + cross-platform
    restore hazard. War-campaign fix E."""
    import tarfile
    from core.backup import _backup_filter
    sym = tarfile.TarInfo("user/plugins/drives")
    sym.type = tarfile.SYMTYPE
    sym.linkname = "/mnt/private"
    assert _backup_filter(sym) is None
    reg = tarfile.TarInfo("user/history/chat.db")
    reg.type = tarfile.REGTYPE
    assert _backup_filter(reg) is not None
