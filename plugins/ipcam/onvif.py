# plugins/ipcam/onvif.py - the ONVIF calls an IP camera needs, by hand.
#
# ONVIF is XML posted over HTTP. A camera has one door (the device service)
# and names its other doors there: media (pictures, streams) and PTZ. Every
# call but the clock carries a WS-Security header: a digest of the password,
# a nonce and the time, so the password itself never travels.
#
# No ONVIF library is used: this is a dozen calls, and the libraries bring a
# WSDL engine with them.
#
# What a camera says is never trusted: every address it names is held to the
# host the user entered (a camera behind NAT names the wrong one anyway), so
# the login is never sent anywhere else.
import base64
import datetime
import hashlib
import os
import re
import socket
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlsplit, urlunsplit
from xml.sax.saxutils import escape

import requests

from core import net

TIMEOUT = 6
MOST = 1024 * 1024            # characters of one answer
USUAL_PORTS = (80, 8899, 8000, 8080, 2020, 8999, 5000, 10080)
PATH = '/onvif/device_service'

_NS = ('xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
       'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
       'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
       'xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" '
       'xmlns:tt="http://www.onvif.org/ver10/schema"')
_WSSE = 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-'
_TIME = ('<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
         '<s:Body><GetSystemDateAndTime xmlns="http://www.onvif.org/ver10/device/wsdl"/></s:Body></s:Envelope>')
_PROBE = ('<?xml version="1.0" encoding="UTF-8"?>'
          '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
          'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
          'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
          'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
          '<e:Header><w:MessageID>uuid:{id}</w:MessageID>'
          '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
          '<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>'
          '</e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>')


class Problem(Exception):
    """A reason fit to show as it is."""


def _parse(text):
    """The answer as a tree with plain tag names, or None."""
    if len(text) > MOST or '<!DOCTYPE' in text or '<!ENTITY' in text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    for el in root.iter():
        el.tag = el.tag.split('}')[-1]
        el.attrib = {k.split('}')[-1]: v for k, v in el.attrib.items()}
    return root


def text_of(root, path):
    el = root.find(path) if root is not None else None
    return (el.text or '').strip() if el is not None else ''


def _post(url, xml):
    """(HTTP status, text). The one function that touches the network."""
    r = net.request('POST', url, data=xml.encode('utf-8'), timeout=TIMEOUT,
                    headers={'Content-Type': 'application/soap+xml; charset=utf-8'})
    return r.status_code, r.text


def _speaks_onvif(host, port):
    try:
        status, text = _post(f"http://{host}:{port}{PATH}", _TIME)
    except requests.exceptions.RequestException:
        return False
    return status == 200 and 'UTCDateTime' in text


def _announced(host, wait):
    """The port the camera names when asked over WS-Discovery, or None."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(wait)
    try:
        s.sendto(_PROBE.format(id=uuid.uuid4()).encode(), (host, 3702))
        data, _ = s.recvfrom(65535)
    except OSError:
        return None
    finally:
        s.close()
    for addr in re.findall(r'XAddrs>([^<]+)<', data.decode('utf-8', 'replace')):
        for one in addr.split():
            try:
                return urlsplit(one).port or 80
            except ValueError:
                continue
    return None


def find(host, wait=2):
    """The port this camera's ONVIF door is on, or None. It is asked first
    (most cameras announce it), then the usual ports are tried."""
    port = _announced(host, wait)
    if port and _speaks_onvif(host, port):
        return port
    for port in USUAL_PORTS:
        if _speaks_onvif(host, port):
            return port
    return None


class Camera:
    """One camera's doors. Cheap to make; `setup()` asks it what it has."""

    def __init__(self, host, port, user, password):
        self.host, self.port, self.user, self.password = host, int(port), user, password or ''
        self.door = f"http://{host}:{self.port}{PATH}"
        self.offset = None          # the camera's clock against ours
        self.media = self.ptz = ''
        self.profiles = []          # [{'token', 'width', 'height', 'ptz', 'stream', 'snapshot'}]

    # --- the wire ---

    def _held(self, url, scheme='http'):
        """An address the camera named, held to the host the user entered.
        '' when it is not of the kind asked for."""
        try:
            parts = urlsplit(str(url or '').strip())
            port = parts.port
        except ValueError:
            return ''
        if parts.scheme != scheme or not parts.path:
            return ''
        return urlunsplit((scheme, self.host + (f":{port}" if port else ''), parts.path, parts.query, ''))

    def _header(self):
        nonce = os.urandom(16)
        now = datetime.datetime.now(datetime.timezone.utc) + (self.offset or datetime.timedelta(0))
        created = now.strftime('%Y-%m-%dT%H:%M:%SZ')
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()).decode()
        return (f'<s:Header><Security s:mustUnderstand="1" xmlns="{_WSSE}wssecurity-secext-1.0.xsd"><UsernameToken>'
                f'<Username>{escape(self.user)}</Username>'
                f'<Password Type="{_WSSE}username-token-profile-1.0#PasswordDigest">{digest}</Password>'
                f'<Nonce EncodingType="{_WSSE}soap-message-security-1.0#Base64Binary">'
                f'{base64.b64encode(nonce).decode()}</Nonce>'
                f'<Created xmlns="{_WSSE}wssecurity-utility-1.0.xsd">{created}</Created>'
                '</UsernameToken></Security></s:Header>')

    def _clock(self):
        """Cameras refuse a login stamped with a time far from their own, and
        their clocks are often wrong: stamp with theirs."""
        self.offset = datetime.timedelta(0)
        try:
            root = _parse(_post(self.door, _TIME)[1])
            u = root.find('.//UTCDateTime')
            g = lambda p: int(text_of(u, p))
            theirs = datetime.datetime(g('Date/Year'), g('Date/Month'), g('Date/Day'), g('Time/Hour'),
                                       g('Time/Minute'), g('Time/Second'), tzinfo=datetime.timezone.utc)
            self.offset = theirs - datetime.datetime.now(datetime.timezone.utc)
        except requests.exceptions.RequestException:
            raise Problem(f"Could not reach the camera at {self.host}:{self.port}. Is it powered and on the network?")
        except (AttributeError, ValueError, TypeError):
            pass                    # it told no time: ours will do

    def call(self, url, body):
        """One call. The answer's tree, or Problem."""
        if self.offset is None:
            self._clock()
        xml = f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope {_NS}>{self._header()}<s:Body>{body}</s:Body></s:Envelope>'
        try:
            status, text = _post(url, xml)
        except requests.exceptions.Timeout:
            raise Problem(f"No answer from the camera at {self.host} within {TIMEOUT}s.")
        except requests.exceptions.RequestException:
            raise Problem(f"Could not reach the camera at {self.host}:{self.port}. Is it powered and on the network?")
        root = _parse(text)
        fault = root.find('.//Fault') if root is not None else None
        if status == 401 or (fault is not None and re.search(
                r'not\s*authori[sz]ed|unauthori[sz]ed|authority|password|authenticat', ' '.join(fault.itertext()), re.I)):
            raise Problem("The camera refused the login. Check the user and password in Settings > Devices.")
        if fault is not None:
            why = ' '.join(t.strip() for t in fault.itertext() if t.strip())
            raise Problem(f"The camera refused that: {why[:160]}")
        if root is None or status != 200:
            raise Problem(f"{self.host}:{self.port} answered, but not like an ONVIF camera (HTTP {status}).")
        return root

    # --- what it has ---

    def info(self):
        root = self.call(self.door, '<tds:GetDeviceInformation/>')
        return {k: text_of(root, f'.//{k}')[:60] for k in ('Manufacturer', 'Model', 'FirmwareVersion')}

    def setup(self):
        """Ask the camera for its doors, its picture profiles, and the address
        of a snapshot and a stream for each."""
        caps = self.call(self.door, '<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>')
        self.media = self._held(text_of(caps, './/Media/XAddr')) or self.door
        self.ptz = self._held(text_of(caps, './/PTZ/XAddr'))
        profiles = []
        for p in self.call(self.media, '<trt:GetProfiles/>').iter('Profiles'):
            token = p.get('token')
            if not token:
                continue
            size = lambda k: int(text_of(p, f'VideoEncoderConfiguration/Resolution/{k}') or 0)
            one = {'token': token, 'width': size('Width'), 'height': size('Height'),
                   'ptz': p.find('PTZConfiguration') is not None, 'stream': '', 'snapshot': ''}
            tok = f'<trt:ProfileToken>{escape(token)}</trt:ProfileToken>'
            try:
                one['stream'] = self._held(text_of(self.call(
                    self.media, '<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>'
                    f'<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>{tok}'
                    '</trt:GetStreamUri>'), './/Uri'), 'rtsp')
            except Problem:
                pass
            try:
                one['snapshot'] = self._held(text_of(self.call(
                    self.media, f'<trt:GetSnapshotUri>{tok}</trt:GetSnapshotUri>'), './/Uri'))
            except Problem:
                pass
            profiles.append(one)
        if not profiles:
            raise Problem("The camera lists no picture profile.")
        profiles.sort(key=lambda q: q['width'] * q['height'], reverse=True)      # sharpest first
        self.profiles = profiles
        if not any(q['ptz'] for q in profiles):
            self.ptz = ''

    def stream_url(self, profile):
        """The stream's address WITH the login in it. Never log it, never return it."""
        parts = urlsplit(profile['stream'])
        login = f"{quote(self.user, safe='')}:{quote(self.password, safe='')}@"
        return urlunsplit((parts.scheme, login + parts.netloc, parts.path, parts.query, ''))

    # --- moving ---

    def _ptz(self, name, inner=''):
        if not self.ptz:
            raise Problem("This camera cannot move.")
        token = next(q['token'] for q in self.profiles if q['ptz'])
        return self.call(self.ptz, f'<tptz:{name}><tptz:ProfileToken>{escape(token)}</tptz:ProfileToken>'
                                   f'{inner}</tptz:{name}>')

    def turn(self, x=0, y=0, zoom=0):
        """Start turning (or zooming) and keep going. The CALLER ends it with
        stop(): ONVIF lets a move carry its own time limit, and cameras exist
        that ignore it and turn until told to stop."""
        inner = f'<tt:PanTilt x="{x}" y="{y}"/>' if x or y else f'<tt:Zoom x="{zoom}"/>'
        self._ptz('ContinuousMove', f'<tptz:Velocity>{inner}</tptz:Velocity>')

    def stop(self):
        self._ptz('Stop', '<tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom>')

    def goto(self, slot):
        self._ptz('GotoPreset', f'<tptz:PresetToken>{int(slot)}</tptz:PresetToken>')

    def save(self, slot):
        self._ptz('SetPreset', f'<tptz:PresetName>{int(slot)}</tptz:PresetName>'
                               f'<tptz:PresetToken>{int(slot)}</tptz:PresetToken>')
