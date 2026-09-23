"""The bot accounts: label → token, kept in core's credentials manager like every
other plugin's secrets (scrambled at rest, outside the project tree). The plugin
owns no database of its own any more; the one-time import below moves the
accounts out of an older install's SQLite file.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_SIDECARS = ('', '-wal', '-shm')


def _plugin_state_dir() -> Path:
    # Walk up with .absolute() (never .resolve(): a symlinked plugin dir would
    # escape the project) to the repo root — the dir holding core/.
    root = Path(__file__).absolute().parent
    while root != root.parent and not (root / 'core').is_dir():
        root = root.parent
    return root / 'user' / 'plugin_state'


class DiscordAccounts:
    def __init__(self, *, store=None, legacy_dir: Path | None = None):
        self._store = store                       # None → core's credentials manager, resolved lazily
        self.legacy_dir = Path(legacy_dir) if legacy_dir else _plugin_state_dir()

    @property
    def store(self):
        if self._store is None:
            from core.credentials_manager import credentials
            self._store = credentials
        return self._store

    # ── the five doors the plugin uses ──

    def list_accounts(self) -> list[dict]:
        return [dict(row) for row in self.store.list_discord_accounts()]

    def get_account(self, name: str) -> dict | None:
        acct = self.store.get_discord_account(str(name or ''))
        if not acct:
            return None
        return {k: v for k, v in acct.items() if k != 'token'}

    def get_token(self, name: str) -> str | None:
        acct = self.store.get_discord_account(str(name or ''))
        return (acct.get('token') or None) if acct else None

    def upsert_account(self, name: str, *, token: str) -> None:
        if not self.store.set_discord_account(str(name), token=str(token)):
            raise RuntimeError(f'could not save Discord account {name!r}')

    def update_connection_state(self, name: str, state: str, *, bot_name: str = '', bot_id: str = '',
                                last_error: str = '') -> None:
        """Live state and last_error stay in the transport's memory; only the
        bot's identity (learned at login) is worth keeping."""
        if bot_name or bot_id:
            self.store.set_discord_account(str(name), bot_name=bot_name, bot_id=bot_id)

    def delete_account(self, name: str) -> bool:
        return bool(self.store.delete_discord_account(str(name)))

    # ── one-time import from the SQLite file older installs kept ──

    def import_legacy(self) -> dict:
        """Move the accounts out of any old plugin database (the 2.0 file or the
        pre-rename `discord_cognitive` one) into the credentials manager, then
        rename the file aside as `.imported`. A file that exists but cannot be
        read refuses the boot — a bot that silently vanished is worse."""
        summary: dict = {}
        for path in (self.legacy_dir / 'discord' / 'discord.sqlite3',
                     self.legacy_dir / 'discord_cognitive' / 'discord.sqlite3'):
            if not path.exists():
                continue
            try:
                conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
                try:
                    has = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='accounts'").fetchone()[0]
                    rows = conn.execute('SELECT name, token, bot_name, bot_id, created_at FROM accounts').fetchall() if has else []
                finally:
                    conn.close()
            except sqlite3.DatabaseError as exc:
                raise RuntimeError(f'Discord: the old account database {path} cannot be read ({exc}) — move it aside '
                                   f'or repair it; no bot was imported') from exc
            copied = 0
            for name, token, bot_name, bot_id, created_at in rows:
                if not name or not token or self.get_token(name):
                    continue
                if not self.store.set_discord_account(str(name), token=str(token), bot_name=str(bot_name or ''),
                                                      bot_id=str(bot_id or ''), created_at=created_at):
                    raise RuntimeError(f'Discord: could not import bot account {name!r} into the credentials manager')
                copied += 1
            aside = path.with_name(path.name + '.imported')
            for suffix in _SIDECARS:
                src = Path(str(path) + suffix)
                if src.exists():
                    shutil.move(str(src), str(aside) + suffix)
            summary[str(path)] = copied
            logger.warning('[DISCORD] imported %d bot account(s) from %s into the credentials manager; the file is '
                           'kept at %s', copied, path, aside)
        return summary
