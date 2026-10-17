"""Shared fakes for the accounts layer (core's credentials doors, in memory)."""
import sqlite3


class FakeCreds:
    """core.credentials_manager's four discord doors, in memory."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def get_discord_account(self, name):
        acct = self.rows.get(name)
        return {'name': name, **acct} if acct else {}

    def set_discord_account(self, name, *, token=None, bot_name=None, bot_id=None, created_at=None):
        acct = dict(self.rows.get(name) or {})
        if token is not None:
            acct['token'] = token
        if bot_name is not None:
            acct['bot_name'] = bot_name
        if bot_id is not None:
            acct['bot_id'] = bot_id
        acct.setdefault('created_at', created_at or 1.0)
        self.rows[name] = acct
        return True

    def delete_discord_account(self, name):
        return self.rows.pop(name, None) is not None

    def list_discord_accounts(self):
        return [{'name': n, 'bot_name': a.get('bot_name', ''), 'bot_id': a.get('bot_id', ''),
                 'created_at': a.get('created_at', 0.0)} for n, a in sorted(self.rows.items())]


def old_account_db(path, rows=(('sapphire', 'tok-1', 'Sapph', '42', 5.0),), with_accounts=True):
    """An accounts table the way the old SQLite file had it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    if with_accounts:
        conn.execute('CREATE TABLE accounts (name TEXT PRIMARY KEY, token TEXT NOT NULL, bot_name TEXT, bot_id TEXT, '
                     'state TEXT, last_error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)')
        for name, token, bot_name, bot_id, created in rows:
            conn.execute('INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (name, token, bot_name, bot_id, 'x', '', created, created))
    conn.execute('CREATE TABLE schema_version (version INTEGER NOT NULL)')
    conn.commit()
    conn.close()
