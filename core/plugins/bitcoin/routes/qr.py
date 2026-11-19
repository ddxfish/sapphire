"""GET /api/plugin/bitcoin/qr/{scope}[?amount=] - a wallet's pay QR code, for
the settings page.

The address comes from the stored key, never from the request, so this door
cannot draw a code that pays anyone but the wallet named.
"""
import base64
import logging

logger = logging.getLogger(__name__)


def wallet_qr(scope, query=None, credentials=None, **_):
    from plugins.bitcoin import qr

    wallet = credentials.get_bitcoin_wallet(scope)
    if not wallet['wif']:
        return {"error": f"No wallet for scope '{scope}'."}, 404
    try:
        from bit import Key
        address = Key(wallet['wif']).address
    except Exception as e:
        # never str(e): bit quotes the whole key in a checksum error
        logger.error(f"[BITCOIN] stored key for scope '{scope}' is not valid ({type(e).__name__})")
        return {"error": "The stored wallet key is not valid."}, 500
    try:
        uri = qr.pay_uri(address, (query or {}).get('amount'))
    except ValueError as e:
        return {"error": str(e)}, 400
    try:
        picture = qr.png(uri)
    except qr.QRUnavailable as e:
        return {"error": str(e), "missing": True}, 503
    return {"address": address, "uri": uri, "png": base64.b64encode(picture).decode('ascii')}
