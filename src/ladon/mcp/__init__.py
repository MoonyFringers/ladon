"""ladon.mcp — MCP adapter protocol for Ladon adapters.

Adapters that want to expose data-plane tools via ladon-nous implement
``LadonMCPAdapter`` and declare themselves via the ``ladon.mcp`` entry point.
"""

from .adapter import LadonMCPAdapter

__all__ = ["LadonMCPAdapter"]
