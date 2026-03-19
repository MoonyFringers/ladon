# Networking API

The networking layer provides a single `HttpClient` that all Ladon crawlers
must use.  Centralising HTTP ensures consistent politeness, retry, and
resilience policies across every plugin.

## HttpClientConfig

::: ladon.networking.config.HttpClientConfig

## HttpClient

::: ladon.networking.client.HttpClient

## Result type

::: ladon.networking.types

## Error types

::: ladon.networking.errors
