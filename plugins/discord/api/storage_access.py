"""SQLite access for API routes when the daemon thread is not running."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from plugins.discord.daemon import get_runtime
from plugins.discord.storage.repositories.accounts import AccountRepository
from plugins.discord.storage.repositories.channels import ChannelRepository
from plugins.discord.storage.repositories.traces import TraceRepository
from plugins.discord.storage.sqlite import SQLiteService, resolve_default_db_path
from plugins.discord.models.settings import SettingsStore


@dataclass
class StorageBundle:
    sqlite_service: SQLiteService
    account_repository: AccountRepository
    channel_repository: ChannelRepository
    trace_repository: TraceRepository
    settings_store: SettingsStore
    owns_sqlite: bool = False
    transport: object | None = None

    def close(self) -> None:
        if self.owns_sqlite:
            self.sqlite_service.stop()


def _bundle_from_runtime(runtime) -> StorageBundle:
    return StorageBundle(
        sqlite_service=runtime.sqlite_service,
        account_repository=runtime.account_repository,
        channel_repository=runtime.channel_repository,
        trace_repository=runtime.trace_repository,
        settings_store=runtime.settings_store or SettingsStore(),
        owns_sqlite=False,
        transport=runtime.transport,
    )


def _bundle_from_sqlite(sqlite: SQLiteService) -> StorageBundle:
    return StorageBundle(
        sqlite_service=sqlite,
        account_repository=AccountRepository(sqlite),
        channel_repository=ChannelRepository(sqlite),
        trace_repository=TraceRepository(sqlite),
        settings_store=SettingsStore(),
        owns_sqlite=True,
        transport=None,
    )


@contextmanager
def open_storage() -> Iterator[StorageBundle]:
    runtime = get_runtime()
    if runtime and runtime.sqlite_service and runtime.account_repository:
        yield _bundle_from_runtime(runtime)
        return
    sqlite = SQLiteService(resolve_default_db_path('discord'))
    sqlite.start()
    bundle = _bundle_from_sqlite(sqlite)
    try:
        yield bundle
    finally:
        bundle.close()
