"""NullRepository — no-op implementation of Repository and RunAudit.

Use for dry runs, search-only crawls, or tests where persistence is not
the subject under test. Also serves as a readable reference template —
a production repository implements the same three methods with real storage.

A WARNING is emitted on construction unless ``silent=True`` is passed.
This makes accidental production use immediately visible in logs.
"""

from __future__ import annotations

import logging

from .record import RunRecord

logger = logging.getLogger(__name__)

_PRODUCTION_WARNING = (
    "NullRepository instantiated: all leaf records and run audit data will "
    "be silently discarded. Pass silent=True to suppress this warning for "
    "intentional dry-run or test usage."
)


class NullRepository:
    """No-op Repository and RunAudit.

    Satisfies both protocols structurally — no inheritance required.

    Args:
        silent: If ``False`` (default), emits a WARNING on construction
                so that accidental production use is visible in logs.
                Pass ``True`` for intentional dry-run or test scenarios.
    """

    def __init__(self, *, silent: bool = False) -> None:
        if not silent:
            logger.warning(_PRODUCTION_WARNING)

    def write_leaf(self, record: object, run_id: str) -> None:
        """No-op. Record is discarded."""

    def record_run(self, run: RunRecord) -> None:
        """No-op. Run audit record is discarded."""

    def get_last_run(
        self, plugin_name: str, status: str | None = "done"
    ) -> RunRecord | None:
        """Always returns None — no run history is stored."""
        return None
