"""Error taxonomy for Ladon house plugins.

Each exception maps to a specific runner behaviour. Keeping these
distinct prevents the catch-all except-Exception pattern that masked
real failures in pre-Ladon crawlers.
"""


class PluginError(Exception):
    """Base class for all plugin-level errors."""


class PreviewAuctionError(PluginError):
    """Auction is not yet live; lot list unavailable.

    The runner should skip this auction without writing to DB or disk.
    Do not retry during the same run; the auction will be discovered
    again on the next nightly run.
    """


class HighlightsOnlyError(PluginError):
    """Auction has a partial lot list (e.g. Phillips HIGHLIGHTS_ONLY).

    The runner should download data to disk but must NOT persist to DB.
    On the next run the auction will be re-evaluated; once the full lot
    list is live, not_seen_before logic will allow a full parse.
    """


class LotListUnavailableError(PluginError):
    """The full lot list could not be retrieved.

    Fatal for this auction run. Raised when the network request
    succeeded but the response cannot be parsed into a usable lot list.
    """


class LotUnavailableError(PluginError):
    """A single lot could not be fetched or parsed.

    Non-fatal. The runner logs the failure, increments lots_failed,
    and continues to the next lot.
    """


class ImageDownloadError(PluginError):
    """An image download failed.

    Non-fatal below the runner's image failure threshold. The runner
    records the failure in the lot's ImageRecord and continues.
    """
