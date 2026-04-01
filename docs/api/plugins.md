# Plugin API

Plugins are the site-specific half of Ladon.  A plugin bundles a `Source`
(discovers top-level refs), one or more `Expanders` (fan out through the
URL tree), and a `Sink` (fetches each leaf and returns a record).  All
three are structural protocols — no inheritance from Ladon is required.

## Protocols

::: ladon.plugins.protocol

## Data models

::: ladon.plugins.models

## Errors

::: ladon.plugins.errors
