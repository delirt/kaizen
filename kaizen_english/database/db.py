"""SQLite connection wrapper with nested transactions and safe migrations."""
from __future__ import annotations

import logging
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from database.migrations import MIGRATIONS

log = logging.getLogger(__name__)


class Database:
    """One shared connection (the bot is a single process; PTB handles updates sequentially)."""

    def __init__(self, path: str):
        self.path = path
        self._memory = path == ":memory:"
        if not self._memory:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        if not self._memory:
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._depth = 0

    # -- transactions -------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN/COMMIT, nestable: only the outermost block really commits."""
        if self._depth == 0:
            self._conn.execute("BEGIN IMMEDIATE")
        self._depth += 1
        try:
            yield self._conn
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self._conn.execute("ROLLBACK")
            raise
        else:
            self._depth -= 1
            if self._depth == 0:
                self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self.transaction() as c:
            return c.execute(sql, params)

    def fetchone(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def scalar(self, sql: str, params: tuple | list = ()) -> Any:
        row = self._conn.execute(sql, params).fetchone()
        return row[0] if row else None

    # -- migrations ---------------------------------------------------------
    def schema_version(self) -> int:
        return self._conn.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> None:
        """Apply pending migrations. Existing data is never dropped; a backup is made first."""
        current = self.schema_version()
        pending = [(v, sql) for v, sql in MIGRATIONS if v > current]
        if not pending:
            return
        if current > 0 and not self._memory:
            self._backup(current)
        for version, sql in pending:
            log.info("Applying DB migration %s", version)
            script = f"BEGIN;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;"
            try:
                self._conn.executescript(script)
            except Exception:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _backup(self, version: int) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = f"{self.path}.bak-v{version}-{stamp}"
        dest = sqlite3.connect(target)
        try:
            self._conn.backup(dest)
        finally:
            dest.close()
        log.info("Database backup written to %s", target)

    def close(self) -> None:
        self._conn.close()
