# Runner API

The runner drives the crawl loop: it expands refs through the plugin's
expander chain and passes each leaf to the sink.

Ladon ships two runners: a synchronous `run_crawl()` and an async
`async_run_crawl()`.  Both use the same `RunConfig` and return the same
`RunResult`.

## run_crawl

::: ladon.runner.run_crawl

## async_run_crawl

::: ladon.async_runner.async_run_crawl

## RunConfig

::: ladon.runner.RunConfig

## RunResult

::: ladon.runner.RunResult
