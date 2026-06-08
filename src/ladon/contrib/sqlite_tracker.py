"""SQLite-backed DecisionTracker reference implementation.

Persists every :class:`~ladon.observability.DecisionEvent` to a local
``decisions`` table, with indexes on ``run_id``, ``ref``, and ``event``
for the common post-run query shapes:

- All decisions for a specific issue: ``SELECT * FROM decisions WHERE ref = ?``
- All decisions from a run: ``SELECT * FROM decisions WHERE run_id = ?``
- Rejection rate by source: ``SELECT source, COUNT(*) FROM decisions WHERE event = 'predicate_rejected' GROUP BY source``

Schema
------
::

    CREATE TABLE decisions (
        id        INTEGER PRIMARY KEY,
        run_id    TEXT NOT NULL,
        timestamp TEXT NOT NULL,   -- ISO-8601 UTC
        ref       TEXT NOT NULL,
        source    TEXT,
        event     TEXT NOT NULL,
        reason    TEXT,
        metadata  TEXT             -- JSON blob
    );

Usage
-----
::

    from ladon.contrib.sqlite_tracker import SqliteDecisionTracker
    from ladon.plugins.resolution import MultiSourceSink

    tracker = SqliteDecisionTracker("decisions.db")
    sink = MyCoverSink(sources=[...], tracker=tracker)
"""

from __future__ import annotations

try:
    import sqlite3
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "sqlite3 is required for SqliteDecisionTracker but is not available "
        "in this Python environment."
    ) from _exc

import json
from pathlib import Path

from ..observability import DecisionEvent

_DDL = """\
CREATE TABLE IF NOT EXISTS decisions (
    id        INTEGER PRIMARY KEY,
    run_id    TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    ref       TEXT NOT NULL,
    source    TEXT,
    event     TEXT NOT NULL,
    reason    TEXT,
    metadata  TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_run   ON decisions (run_id);
CREATE INDEX IF NOT EXISTS idx_decisions_ref   ON decisions (ref);
CREATE INDEX IF NOT EXISTS idx_decisions_event ON decisions (event);
"""

_INSERT = """\
INSERT INTO decisions (run_id, timestamp, ref, source, event, reason, metadata)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""


class SqliteDecisionTracker:
    """Append-only SQLite tracker.

    Opens (or creates) *db_path* on construction and initialises the schema.
    Writes are synchronous — suitable for single-threaded adapters.

    Args:
        db_path: Path to the SQLite database file.  Created if it does not
                 exist.  Pass ``":memory:"`` for ephemeral in-memory storage
                 (useful in tests).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(_DDL)
        self._conn.commit()

    def record(self, event: DecisionEvent) -> None:
        """Insert one decision event into the ``decisions`` table.

        Each call commits immediately for durability.  For single-threaded
        adapters processing a few hundred items this is negligible; it is
        not appropriate for high-throughput or concurrent use.
        """
        self._conn.execute(
            _INSERT,
            (
                event.run_id,
                event.timestamp.isoformat(),
                event.ref,
                event.source,
                event.event,
                event.reason,
                json.dumps(event.metadata) if event.metadata else None,
            ),
        )
        self._conn.commit()

    def query(
        self, sql: str, params: tuple[object, ...] = ()
    ) -> list[tuple[object, ...]]:
        """Execute *sql* with *params* and return all rows.

        Intended for post-run analysis and testing.  The ``decisions`` table
        schema is stable — callers may rely on column names and indexes.
        """
        return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()

    def __enter__(self) -> SqliteDecisionTracker:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
