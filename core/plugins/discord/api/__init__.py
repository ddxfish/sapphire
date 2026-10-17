# Route handlers here are plain `def` ON PURPOSE. Core's dispatcher
# (core/routes/plugins.py plugin_route_dispatch) awaits coroutine handlers
# INLINE on Sapphire's main web loop and threadpools sync ones. Anything that
# touches sqlite, waits on a daemon-loop future, or calls an LLM must be sync —
# as `async def` they froze the whole web UI for up to 60s (hunt 2026-09-12,
# H12). Only handlers that genuinely await (the aiohttp token check) stay async.
