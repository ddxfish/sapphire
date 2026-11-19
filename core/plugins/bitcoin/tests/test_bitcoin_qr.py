"""The pay QR code: the amount rules, the pay link, the picture, the tool and
the settings page's door.

No test reaches a network or the credential store: the wallet is a key made
here, and the one test that goes through the store's import gets a stand-in
module. The read-it-back tests need a QR decoder (zxing-cpp), which Sapphire
does not ship, so they skip without one. Only the tests that draw a picture
need segno; the rest run without it.
"""
import base64
import io
import sys
from types import SimpleNamespace

import pytest

bit = pytest.importorskip('bit')

from plugins.bitcoin import qr                      # noqa: E402
from plugins.bitcoin.tools import bitcoin_tool      # noqa: E402

# a real key with its last character changed: bit's checksum error quotes it whole
BAD_WIF = '5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTK'


def _read_back(png_bytes):
    zxingcpp = pytest.importorskip('zxingcpp')
    from PIL import Image
    found = zxingcpp.read_barcodes(Image.open(io.BytesIO(png_bytes)))
    assert len(found) == 1
    return found[0].text


@pytest.fixture
def segno():
    return pytest.importorskip('segno')


@pytest.fixture
def wallet(monkeypatch):
    key = bit.Key()
    monkeypatch.setattr(bitcoin_tool, '_get_wallet_key', lambda: (key, None))
    monkeypatch.setattr(bitcoin_tool, '_get_current_bitcoin_scope', lambda: 'test')
    return key


def _creds(wif):
    return SimpleNamespace(get_bitcoin_wallet=lambda scope: {'label': scope, 'wif': wif})


# ── the amount ───────────────────────────────────────────────────────────

@pytest.mark.parametrize('given,want', [
    ('0.001', '0.001'), ('1', '1'), ('0.10', '0.1'), ('100', '100'), (' 2 ', '2'),
    ('1e-5', '0.00001'), (0.5, '0.5'), (1e-05, '0.00001'),
    ('0.00000001', '0.00000001'), ('21000000', '21000000'),
])
def test_amount_is_plain_decimal_btc(given, want):
    assert qr.clean_amount(given) == want


@pytest.mark.parametrize('nothing', [None, '', '   '])
def test_no_amount_is_none(nothing):
    assert qr.clean_amount(nothing) is None


@pytest.mark.parametrize('bad', ['abc', '0', '-1', 'NaN', 'Infinity', '-Infinity', '1,5',
                                 '0.000000001', '21000001', '1e99', '0.001 BTC'])
def test_bad_amount_refuses_rather_than_rounds(bad):
    with pytest.raises(ValueError):
        qr.clean_amount(bad)


def test_pay_link():
    assert qr.pay_uri('1abc') == 'bitcoin:1abc'
    assert qr.pay_uri('1abc', '') == 'bitcoin:1abc'
    assert qr.pay_uri('1abc', '0.0050') == 'bitcoin:1abc?amount=0.005'
    with pytest.raises(ValueError):
        qr.pay_uri('1abc', 'lots')


# ── the picture ──────────────────────────────────────────────────────────

def test_png_is_black_on_white_with_a_quiet_border(segno):
    from PIL import Image
    raw = qr.png('bitcoin:' + bit.Key().address)
    assert raw.startswith(b'\x89PNG\r\n\x1a\n')
    img = Image.open(io.BytesIO(raw)).convert('RGB')
    assert img.width == img.height
    assert {colour for _, colour in img.getcolors()} == {(0, 0, 0), (255, 255, 255)}
    border = 4 * 8      # four modules at scale 8
    assert all(img.getpixel((x, y)) == (255, 255, 255)
               for x in range(img.width) for y in list(range(border)) + list(range(img.height - border, img.height)))
    assert all(img.getpixel((x, y)) == (255, 255, 255)
               for y in range(img.height) for x in list(range(border)) + list(range(img.width - border, img.width)))


@pytest.mark.parametrize('amount', [None, '0.001', '20999999.99999999'])
def test_png_reads_back_as_the_exact_pay_link(amount, segno):
    key = bit.Key()
    uri = qr.pay_uri(key.address, amount)
    assert _read_back(qr.png(uri)) == uri
    assert uri.split('?')[0] == 'bitcoin:' + key.address


def test_missing_segno_is_its_own_error(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_segno(name, *a, **k):
        if name == 'segno':
            raise ImportError('No module named segno')
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, '__import__', no_segno)
    with pytest.raises(qr.QRUnavailable, match='segno'):
        qr.png('bitcoin:1abc')


# ── the tool ─────────────────────────────────────────────────────────────

def test_tool_shows_the_user_the_code_and_tells_her_the_address(wallet, segno):
    out, ok = bitcoin_tool.execute('bitcoin_receive', {}, None)
    assert ok
    assert f"Address: {wallet.address}" in out['text']
    assert f"Pay link: bitcoin:{wallet.address}\n" in out['text']
    [img] = out['images']
    assert img['media_type'] == 'image/png'
    assert img['display_only'] is True          # a picture for the user, not for the model
    assert base64.b64decode(img['data']).startswith(b'\x89PNG')


def test_tool_code_reads_back_as_her_own_address(wallet, segno):
    out, ok = bitcoin_tool.execute('bitcoin_receive', {'amount': '0.0025'}, None)
    assert ok
    assert _read_back(base64.b64decode(out['images'][0]['data'])) == f"bitcoin:{wallet.address}?amount=0.0025"


def test_tool_refuses_a_bad_amount_in_words(wallet):
    out, ok = bitcoin_tool.execute('bitcoin_receive', {'amount': 'a few'}, None)
    assert not ok and isinstance(out, str) and 'Invalid amount' in out


def test_tool_without_segno_still_gives_the_address(wallet, monkeypatch):
    def gone(text, scale=8):
        raise qr.QRUnavailable("no segno")
    monkeypatch.setattr(qr, 'png', gone)
    out, ok = bitcoin_tool.execute('bitcoin_receive', {}, None)
    assert not ok and isinstance(out, str)
    assert wallet.address in out and 'No QR code was shown' in out


def test_tool_with_no_wallet_says_so(monkeypatch):
    monkeypatch.setattr(bitcoin_tool, '_get_wallet_key', lambda: (None, "No Bitcoin wallet configured"))
    assert bitcoin_tool.execute('bitcoin_receive', {}, None) == ("No Bitcoin wallet configured", False)


def test_receive_is_local_and_the_money_tools_are_not():
    local = {t['function']['name']: t['is_local'] for t in bitcoin_tool.TOOLS}
    assert local == {'get_wallet': False, 'bitcoin_receive': True,
                     'send_bitcoin': False, 'get_transactions': False}
    assert set(bitcoin_tool.AVAILABLE_FUNCTIONS) == set(local)


def test_a_bad_stored_key_never_reaches_the_model(monkeypatch, caplog):
    """bit quotes the whole key in its checksum error. That text used to go
    into the tool result, so to the model and into chat history."""
    with pytest.raises(ValueError, match=BAD_WIF):       # the trap is real
        bit.Key(BAD_WIF)
    # a stand-in module: importing the real one would open the real credential file
    monkeypatch.setitem(sys.modules, 'core.credentials_manager', SimpleNamespace(credentials=_creds(BAD_WIF)))
    monkeypatch.setattr(bitcoin_tool, '_get_current_bitcoin_scope', lambda: 'test')
    with caplog.at_level('DEBUG'):
        key, err = bitcoin_tool._get_wallet_key()
    assert key is None and 'not valid' in err
    assert BAD_WIF not in err
    assert BAD_WIF not in caplog.text


# ── the settings page's door ─────────────────────────────────────────────

@pytest.fixture
def door():
    import plugins.bitcoin.routes.qr as routes
    return routes.wallet_qr


def test_door_draws_the_stored_wallets_own_address(door, segno):
    key = bit.Key()
    out = door('main', query={'amount': '0.5'}, credentials=_creds(key.to_wif()))
    assert out['address'] == key.address
    assert out['uri'] == f"bitcoin:{key.address}?amount=0.5"
    assert base64.b64decode(out['png']).startswith(b'\x89PNG')
    assert _read_back(base64.b64decode(out['png'])) == out['uri']


def test_door_ignores_any_address_in_the_request(door, segno):
    key = bit.Key()
    out = door('main', query={'address': '1SomeoneElse', 'amount': ''}, credentials=_creds(key.to_wif()),
               body={'address': '1SomeoneElse'}, settings={}, request=None)
    assert out['uri'] == f"bitcoin:{key.address}"


def test_door_refusals(door, monkeypatch):
    key = bit.Key()
    assert door('none', query={}, credentials=_creds(''))[1] == 404
    out, status = door('main', query={'amount': '-1'}, credentials=_creds(key.to_wif()))
    assert status == 400 and 'positive' in out['error']
    out, status = door('main', query={}, credentials=_creds(BAD_WIF))
    assert status == 500 and BAD_WIF not in out['error']

    def gone(text, scale=8):
        raise qr.QRUnavailable("no segno")
    monkeypatch.setattr(qr, 'png', gone)
    out, status = door('main', query={}, credentials=_creds(key.to_wif()))
    assert status == 503 and out['missing'] is True
