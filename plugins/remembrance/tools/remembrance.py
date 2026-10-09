# plugins/remembrance/tools/remembrance.py — Sapphire's offsite-vault tool.
# One dual-purpose tool: no comment → vault status; with a comment → back up now.
import logging

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = "🛰️"
TOOL_CATEGORY = 'system'

AVAILABLE_FUNCTIONS = ["remembrance_backup"]

TOOLS = [
    {"type": "function", "writes": True, "is_local": False, "network": True, "function": {
        "name": "remembrance_backup",
        "description": (
            "Offsite encrypted backup vault. No comment = vault status (storage, quota, recent backups). With a "
            "comment = make and upload a fresh encrypted backup labeled with it; labeled backups are kept "
            "long-term."),
        "parameters": {"type": "object", "properties": {
            "comment": {"type": "string", "description":
                        "Label for a new backup. Omit for status."}
        }, "required": []}}},
]


def _mb(n):
    try:
        return f"{int(n) // (1024 * 1024)}"
    except Exception:
        return "?"


def execute(function_name, arguments, config, plugin_settings=None):
    if function_name != "remembrance_backup":
        return f"Unknown function: {function_name}", False
    from plugins.remembrance import ops

    comment = (arguments or {}).get("comment")
    if comment:
        r = ops.perform_offsite_backup(cadence="manual", comment=comment)
        if not r.get("ok"):
            return f"Offsite backup failed: {r.get('error')}", False
        return (f"Offsite backup uploaded (id {str(r.get('id', '?'))[:8]}, {r.get('size_bytes', 0) // 1024} KB). "
                f"Vault: {_mb(r.get('usage_bytes'))} / {_mb(r.get('quota_bytes'))} MB used.", True)

    r = ops.get_status()
    if not r.get("ok"):
        return f"Vault status unavailable: {r.get('error')}", False
    backups = r.get("backups", [])
    latest = backups[0].get("created_at", "?") if backups else "none yet"
    return (f"Remembrance vault: {_mb(r.get('usage_bytes'))} / {_mb(r.get('quota_bytes'))} MB used, "
            f"{len(backups)} backup(s). Latest: {latest}.", True)
