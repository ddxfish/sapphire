"""SQLite access for API routes when the daemon thread is not running."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from plugins.discord.daemon import get_runtime
from plugins.discord.storage.repositories import AccountRepository, ChannelRepository
from plugins.discord.storage.sqlite import SQLiteService, resolve_default_db_path


@dataclass
class StorageBundle:
    sqlite_service: SQLiteService
    account_repository: AccountRepository
    channel_repository: ChannelRepository
    owns_sqlite: bool = False
    transport: object | None = None

    def close(self) -> None:
        if self.owns_sqlite:
            self.sqlite_service.stop()


@contextmanager
def open_storage() -> Iterator[StorageBundle]:
    runtime = get_runtime()
    if runtime is not None:
        yield StorageBundle(sqlite_service=runtime.sqlite_service, account_repository=runtime.account_repository,
                            channel_repository=runtime.channel_repository, transport=runtime.transport)
        return
    sqlite = SQLiteService(resolve_default_db_path('discord'))
    sqlite.start()
    bundle = StorageBundle(sqlite_service=sqlite, account_repository=AccountRepository(sqlite),
                           channel_repository=ChannelRepository(sqlite), owns_sqlite=True)
    try:
        yield bundle
    finally:
        bundle.close()
