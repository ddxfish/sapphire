"""Twilio account CRUD — feeds the daemon-task account dropdown + the settings UI.

Served at /api/plugin/twilio-voice/accounts (GET list, POST save, DELETE by scope).
Accounts live in credentials_manager.twilio_accounts (sip_pass scrambled at rest).
"""
import logging

logger = logging.getLogger(__name__)


def list_accounts(credentials=None, **_):
    """GET /api/plugin/twilio-voice/accounts -> {accounts: [...]} (no secrets)."""
    if credentials is None:
        return {"accounts": []}
    accounts = credentials.list_twilio_accounts()
    for a in accounts:
        # `badge`: shown by the Realtime editor under the number picker — the
        # one place the owner looks when wiring a rule, so elevation state is
        # visible where it matters (never the key itself).
        if a.get("elevate_configured"):
            lock = (a.get("elevate_toolset") or "").strip()
            a["badge"] = ("\U0001F511 Elevation key set — the elevate tool rides this "
                          "number's inbound calls, even with tools 'none'. "
                          + (f"Unlocks '{lock}' only." if lock else
                             "No unlock toolset set on the account yet — a matched "
                             "key will have nothing to open."))
    return {"accounts": accounts}


def save_account(body=None, credentials=None, **_):
    """POST — create/update an account.
    Body: {scope, sip_domain, sip_user, sip_pass, number?, chat?, greeting?, transport?}.
    Empty sip_pass on update keeps the stored one (don't clobber with blank)."""
    body = body or {}
    scope = (body.get("scope") or "").strip()
    sip_domain = (body.get("sip_domain") or "").strip()
    sip_user = (body.get("sip_user") or "").strip()
    sip_pass = (body.get("sip_pass") or "").strip()
    if not (scope and sip_domain and sip_user):
        return {"ok": False, "error": "scope, sip_domain, sip_user are required"}
    existing = credentials.get_twilio_account(scope)
    if not sip_pass:                                        # keep existing on blank
        sip_pass = existing.get("sip_pass", "")
        if not sip_pass:
            return {"ok": False, "error": "sip_pass required for a new account"}
    # REST creds (outbound calling) are optional; blank auth_token keeps stored.
    auth_token = (body.get("auth_token") or "").strip() or existing.get("auth_token", "")
    # Elevation passphrase: blank keeps stored; the literal sentinel "-" clears it
    # (the UI never round-trips the key itself, so blank can't mean "remove").
    elevate_key = (body.get("elevate_key") or "").strip() or existing.get("elevate_key", "")
    if elevate_key == "-":
        elevate_key = ""
    ok = credentials.set_twilio_account(
        scope, sip_domain=sip_domain, sip_user=sip_user, sip_pass=sip_pass,
        number=(body.get("number") or "").strip(),
        chat=(body.get("chat") or "default").strip(),
        greeting=(body.get("greeting") or "").strip(),
        account_sid=(body.get("account_sid") or "").strip(),
        auth_token=auth_token,
        transport=(body.get("transport") or "tls").strip(),
        call_provider=(body.get("call_provider") or "").strip(),
        call_model=(body.get("call_model") or "").strip(),
        elevate_key=elevate_key,
        elevate_toolset=(body.get("elevate_toolset") or "").strip())
    return {"ok": bool(ok)}


def delete_account(scope=None, credentials=None, **_):
    """DELETE /api/plugin/twilio-voice/accounts/{scope}."""
    if not scope:
        return {"ok": False, "error": "no scope"}
    return {"ok": bool(credentials.delete_twilio_account(scope))}
