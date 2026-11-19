# The pay QR code: a wallet's receive address as a picture a phone can scan.
"""`bitcoin:<address>[?amount=<btc>]` (BIP21), drawn by segno.

segno is the plugin's optional package (plugin.json `extra`): without it the
wallet tools still work and only the picture refuses, with QRUnavailable.
"""
import io
from decimal import Decimal, InvalidOperation

ALL_BTC = Decimal(21_000_000)
SATOSHI = Decimal('0.00000001')


class QRUnavailable(Exception):
    """segno is not installed. The message is fit to show."""


def clean_amount(amount):
    """BTC as BIP21 wants it: plain decimal, no exponent. Nothing -> None.
    Raises ValueError (fit to show) rather than round someone's money."""
    text = '' if amount is None else str(amount).strip()
    if not text:
        return None
    try:
        btc = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"Invalid amount: {text}. Use decimal BTC (e.g. '0.001').")
    if not btc.is_finite() or btc <= 0:
        raise ValueError("Amount must be positive.")
    if btc > ALL_BTC:
        raise ValueError(f"{text} BTC is more than all the Bitcoin there is.")
    if btc != btc.quantize(SATOSHI):
        raise ValueError(f"{text} is finer than one satoshi (8 decimal places).")
    return format(btc.normalize(), 'f')


def pay_uri(address, amount=None):
    """The text inside the QR code. Raises ValueError on a bad amount."""
    btc = clean_amount(amount)
    return f"bitcoin:{address}" + (f"?amount={btc}" if btc else "")


def png(text, scale=8):
    """A QR code of `text` as PNG bytes: black on white, with the quiet border
    a scanner needs, so it reads on a dark theme too."""
    try:
        import segno
    except ImportError:
        raise QRUnavailable("The QR code maker (the 'segno' package) is not installed. "
                            "Open a wallet in Settings > Plugins > Bitcoin and click Install.")
    out = io.BytesIO()
    # micro=False: phone wallets do not read Micro QR
    segno.make(text, error='m', micro=False).save(
        out, kind='png', scale=scale, border=4, dark='#000', light='#fff')
    return out.getvalue()
