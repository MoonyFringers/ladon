"""Command-line interface for Ladon.

Why this module exists
----------------------
Ladon has no command-line entry point, which means the only way to run a crawl
is to write a Python script that imports and calls the library directly.  This
creates a significant barrier to adoption:

* New users cannot try Ladon without writing code first.
* There is no standard way to invoke a plugin from CI/CD or shell scripts.
* Discoverability suffers — ``pip install ladon && ladon --help`` works for
  most well-known frameworks; Ladon should be no exception.

Design decisions
----------------
* **argparse only** — no Click, Typer, or other CLI library.  The stdlib is
  sufficient for the v1 command surface, and adding a CLI dependency purely for
  syntax sugar would conflict with Ladon's "no unnecessary dependencies" policy.
* **Dynamic plugin import via dotted path** — ``ladon run --plugin a.b:Class``
  mirrors the pattern used by tools like Gunicorn and Celery.  It lets operators
  specify any importable plugin without Ladon needing to know about it at
  install time, which is essential because site adapters live in separate repos.
* **Structured text output** — ``ladon run`` prints a machine-readable summary
  of the ``RunResult`` fields.  This makes it easy to grep / pipe results in
  CI without committing to a full JSON serialisation format at v1.
* **Version via importlib.metadata** — the version string is kept in exactly
  one place (``pyproject.toml``); importlib.metadata reads it at runtime so
  the CLI never goes out of sync.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import Any, NoReturn
from urllib.parse import urlparse


def _ladon_version() -> str:
    """Return the Ladon version string.

    Falls back to ``ladon.__version__`` when the package is not installed
    (e.g. running from a source checkout without ``pip install -e .``).
    """
    try:
        return pkg_version("ladon-crawl")
    except PackageNotFoundError:
        from ladon import __version__

        return __version__


def load_plugin_class(dotted: str) -> type[Any]:
    """Import a plugin class from a ``module.path:ClassName`` string.

    Args:
        dotted: Dotted module path followed by ``:`` and the class name,
            e.g. ``mypackage.adapters.example:ExamplePlugin``.

    Returns:
        The loaded class object.

    Raises:
        SystemExit: If the string is malformed, the module cannot be imported,
            or the attribute does not exist on the module.
    """
    if dotted.count(":") != 1:
        _die(
            f"Invalid plugin specifier {dotted!r}. "
            "Expected format: module.path:ClassName"
        )
    module_path, class_name = dotted.split(":", 1)
    try:
        module = importlib.import_module(module_path)
    except (ImportError, ValueError) as exc:
        _die(f"Cannot import module {module_path!r}: {exc}")
    else:
        try:
            return getattr(module, class_name)
        except AttributeError:
            _die(f"Module {module_path!r} has no attribute {class_name!r}")


def _die(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Sub-command handlers
# ---------------------------------------------------------------------------


def _cmd_info(_args: argparse.Namespace) -> None:
    """Print runtime environment information."""
    import platform

    print(f"ladon      {_ladon_version()}")
    print(f"python     {platform.python_version()}")
    print(f"platform   {platform.platform()}")


def _cmd_run(args: argparse.Namespace) -> None:
    """Dynamically import a plugin and run a crawl against *ref*.

    The plugin class must conform to the ``CrawlPlugin`` protocol and must
    accept ``client`` as a keyword argument in its ``__init__`` method — the
    CLI constructs it as ``plugin_cls(client=client)``.  Default
    ``RunConfig`` settings are used; for fine-grained control write a Python
    script that calls ``run_crawl()`` directly.

    Exit codes
    ----------
    0 — all leaves fetched successfully
    1 — unrecoverable error (bad plugin specifier, import failure, run exception)
    2 — run completed with partial failures (``leaves_failed > 0`` or errors)
    3 — ``ExpansionNotReadyError``: data not yet available; caller should retry later
    """
    from ladon.networking.client import HttpClient
    from ladon.networking.config import HttpClientConfig
    from ladon.plugins.errors import ExpansionNotReadyError
    from ladon.runner import RunConfig, run_crawl

    ref: str = args.ref
    parsed_ref = urlparse(ref)
    if not parsed_ref.scheme or not parsed_ref.netloc:
        _die(
            f"Invalid --ref URL {ref!r}. "
            "Expected an absolute URL, e.g. https://example.com/catalogue"
        )
    if parsed_ref.scheme not in ("http", "https"):
        _die(
            f"Invalid --ref URL {ref!r}. "
            f"Scheme {parsed_ref.scheme!r} is not supported; use http or https"
        )

    plugin_cls = load_plugin_class(args.plugin)

    # Lazy imports above keep module-level import cost low and make _cmd_run
    # independently testable.  Do not hoist them to module level — HttpClient
    # and run_crawl are only needed when this sub-command is actually invoked.
    with HttpClient(
        HttpClientConfig(respect_robots_txt=args.respect_robots_txt)
    ) as client:
        # _die() raises SystemExit which propagates through HttpClient.__exit__
        # via BaseException, so the session is always closed even on early exit.
        try:
            plugin = plugin_cls(client=client)
        except Exception as exc:
            _die(f"Failed to instantiate plugin {args.plugin!r}: {exc}")

        try:
            result = run_crawl(
                top_ref=args.ref,
                plugin=plugin,
                client=client,
                config=RunConfig(),
            )
        except ExpansionNotReadyError as exc:
            print(
                f"error: data not ready — retry later: {exc}", file=sys.stderr
            )
            sys.exit(3)
        except Exception as exc:
            _die(f"Run failed: {exc}")

        print(f"leaves_consumed   {result.leaves_consumed}")
        print(f"leaves_persisted  {result.leaves_persisted}")
        print(f"leaves_failed     {result.leaves_failed}")
        print(f"errors            {len(result.errors)}")
        if result.errors:
            for err in result.errors:
                print(f"  - {err}")

        if result.leaves_failed > 0 or result.errors:
            sys.exit(2)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ladon",
        description="Ladon — resilient, extensible web crawling framework.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"ladon {_ladon_version()}",
    )

    subparsers = parser.add_subparsers(dest="command")

    # info
    subparsers.add_parser("info", help="Print runtime environment information.")

    # run
    run_parser = subparsers.add_parser(
        "run",
        help="Run a crawl using a plugin class.",
        description=(
            "Run a crawl using a plugin class. "
            "Uses default HttpClientConfig settings (30 s timeout, no retries, "
            "no rate limiting, no robots.txt enforcement). "
            "For fine-grained control call run_crawl() directly from Python."
        ),
    )
    run_parser.add_argument(
        "--plugin",
        required=True,
        metavar="MODULE:CLASS",
        help=(
            "Dotted import path to the CrawlPlugin class, "
            "e.g. mypackage.adapters:MyPlugin"
        ),
    )
    run_parser.add_argument(
        "--ref",
        required=True,
        metavar="URL",
        help="Top-level reference URL to pass to the plugin.",
    )
    run_parser.add_argument(
        "--respect-robots-txt",
        action="store_true",
        default=False,
        dest="respect_robots_txt",
        help=(
            "Honour robots.txt Disallow rules and Crawl-delay directives. "
            "Disabled by default for backward compatibility; "
            "strongly recommended for public-web crawls."
        ),
    )

    return parser


def main() -> None:
    """Entry point for the ``ladon`` command-line tool."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "info":
        _cmd_info(args)
    elif args.command == "run":
        _cmd_run(args)
    else:
        parser.print_help()
        sys.exit(1)
