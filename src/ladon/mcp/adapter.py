"""LadonMCPAdapter — base class for MCP data-plane adapter plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable


class LadonMCPAdapter(ABC):
    """Base class for Ladon adapter packages that expose MCP tools.

    Implement this in each adapter package (ladon-mimir, ladon-hermes, …) and
    declare the concrete class via the ``ladon.mcp`` Python entry-point group::

        # pyproject.toml
        [project.entry-points."ladon.mcp"]
        mimir = "ladon_mimir.mcp:MimirMCPAdapter"

    ``ladon-nous`` discovers all registered adapters at startup and registers
    their tools and resources on the FastMCP server automatically.

    ABC is used (rather than a Protocol) because adapters explicitly opt in to
    the MCP system — they know they are implementing this contract. ABC gives a
    clear error at class-definition time when ``mcp_tools`` is forgotten.
    """

    def __init__(self, db_path: str) -> None:
        # v0.2: all adapters receive the same --db path from ladon-nous.
        # Multi-path support (--db adapter=path) is planned for v0.3.
        self.db_path = db_path

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Short identifier used in log messages and reserved for future
        tool-name prefixing: ``mimir``, ``hermes``."""
        ...

    @abstractmethod
    def mcp_tools(self) -> list[Callable[..., object]]:
        """Plain callables to register as MCP tools on the ladon-nous server.

        Each callable must have a descriptive docstring (used as the tool
        description) and type-annotated parameters (used to build the JSON
        schema). All database access **must** use ``read_only=True``.

        On ``duckdb.Error``, return a structured ``{"error": "..."}`` payload
        (or ``[{"error": "..."}]`` for list-returning tools) rather than
        raising — MCP clients may not handle exceptions gracefully.
        """
        ...

    def mcp_resources(self) -> list[tuple[str, Callable[..., object]]]:
        """Resource (uri_template, handler_fn) pairs to register on the server.

        The URI template follows FastMCP syntax. Path parameter names in the
        template **must match** the handler function's parameter names exactly —
        a mismatch fails silently at call time, not at registration.

        Example::

            def article_handler(page_id: int) -> str:
                \"\"\"Full article content.\"\"\"
                ...
            return [("ladon://mimir/articles/{page_id}", article_handler)]

        All database access in the handler **must** use ``read_only=True``.
        """
        return []
