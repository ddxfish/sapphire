from plugins.discord.storage.repositories.accounts import AccountRepository
from plugins.discord.storage.sqlite import SQLiteService


def test_storage_bootstrap_and_migrations_are_idempotent(tmp_path):
    db_path = tmp_path / "discord.sqlite3"
    service = SQLiteService(db_path)
    service.start()
    service.start()

    row = service.connection().execute("SELECT version FROM schema_version").fetchone()
    assert row[0] >= 1

    tables = {r[0] for r in service.connection().execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "accounts" in tables
    assert "settings_overrides" in tables


def test_account_repository_round_trip(tmp_path):
    service = SQLiteService(tmp_path / "discord.sqlite3")
    service.start()
    repo = AccountRepository(service)
    repo.upsert_account("alpha", token="secret", bot_name="Alpha", bot_id="123")

    account = repo.get_account("alpha")
    assert account["name"] == "alpha"
    assert account["bot_name"] == "Alpha"

    names = [item["name"] for item in repo.list_accounts()]
    assert names == ["alpha"]
