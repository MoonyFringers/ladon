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
    """The expansion returned an incomplete child list.

    The runner should download data to disk but must NOT persist to DB.
    On the next run the ref will be re-evaluated; once the full child
    list is live, not-seen-before logic will allow a full parse.
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


class AssetDownloadError(PluginError):
    """An asset download failed.

    Non-fatal below the runner's asset failure threshold. The runner
    records the failure and continues.
    """
