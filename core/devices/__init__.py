"""Devices - machines and gadgets Sapphire can use by name.

    registry.py      the drivers that plugins declare (capabilities.devices)
    secret_store.py  device passwords, keys and tokens, in their own file
    engine.py        the device list, status, and the dispatch behind the tools

Doors into the engine: the three tools (functions/devices.py), the routes
(core/routes/devices.py) and the Settings > Devices tab. Drivers stay in
plugins, so hardware code and its dependencies stay out of core.

Nothing here is imported at boot except registry.py (by the plugin loader).
The engine loads on first use, so a fault in it cannot stop Sapphire starting.
Plan and build log: tmp/device-manager-plan.md. Author guide:
docs/plugin-author/devices.md.
"""
