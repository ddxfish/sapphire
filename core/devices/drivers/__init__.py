"""Device drivers that ship inside core.

A driver lives here only when core itself depends on it. The satellite is
the first: voice is a core pipeline and a satellite is its far end. Every
other driver belongs in a plugin (docs/plugin-author/devices.md).

Each module carries its declaration as SPEC (the manifest entry a plugin
would write) and the same functions a plugin driver has. Their ids are
listed in registry.CORE_DRIVERS so no plugin can claim one. Nothing here is
imported at boot: the engine loads a driver on first use.
"""
