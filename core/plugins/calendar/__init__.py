"""Local-first calendar for Sapphire: events live in the plugin's own SQLite
(user/plugin_state/calendar_events.db). The page (app/) and her tools (tools/)
both go through api.py. Sources (Google, ICS, CalDAV) attach later through the
`source` column; this build is the local store only."""
