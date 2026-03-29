"""Error taxonomy for Ladon house plugins.

Each exception maps to a specific runner behaviour. Keeping these
distinct prevents the catch-all except-Exception pattern that masked
real failures in pre-Ladon crawlers.
"""


class PluginError(Exception):
    """Base class for all plugin-level errors."""


class ExpansionNotReadyError(PluginError):
    """The ref is not yet ready to be expanded (e.g. content not live).

    The runner should skip this ref without writing to DB or disk.
    Do not retry during the same run; the ref will be discovered again
    on the next scheduled run.
    """


class PartialExpansionError(PluginError):
    """The child list was fetched but is incomplete (e.g. a paginated
    response returned fewer items than the declared total).

    Runner behaviour: non-fatal for non-first expanders — the affected
    branch is isolated and recorded in ``RunResult.errors``. Propagates
    unchanged from the first expander.

    Raise this instead of ``ChildListUnavailableError`` when the HTTP
    response was valid but the payload signals an incomplete result.
    Raise ``ChildListUnavailableError`` when the response could not be
    parsed or the request itself failed.
    """


class ChildListUnavailableError(PluginError):
    """The child list could not be retrieved.

    Fatal for this ref's run. Raised when the network request succeeded
    but the response cannot be parsed into a usable child list.
    """


class LeafUnavailableError(PluginError):
    """A single leaf ref could not be fetched or parsed.

    Non-fatal. The runner logs the failure, increments leaves_failed,
    and continues to the next leaf.
    """


# ---------------------------------------------------------------------------
# Plugin-use errors NOT caught by the runner
# ---------------------------------------------------------------------------
# Unlike the errors above, AssetDownloadError is not handled by run_crawl().
# If raised from a Sink or Expander it propagates as a fatal error and aborts
# the entire run.  Plugins that need non-fatal asset download handling must
# catch it internally before returning.
# ---------------------------------------------------------------------------


class AssetDownloadError(PluginError):
    """An asset download failed.

    **Not caught by the runner** — propagates as a fatal error that aborts
    the run.  Plugins requiring non-fatal handling must catch this exception
    internally before returning from the Sink or Expander.
    """
