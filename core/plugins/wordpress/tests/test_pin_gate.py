"""PIN gate + burn-on-use logic for the WordPress plugin.

These are the security-critical paths: a destructive op must be refused without
a valid PIN (supervised, the default), allowed without one only when the user
explicitly opted into unsupervised mode, and the PIN must rotate after use.
All mocked - no live WordPress, no plugin loader.
"""
from unittest.mock import patch

import plugins.wordpress.tools.wordpress as wp


class FakeState:
    """In-memory stand-in for PluginState (get/save)."""
    def __init__(self, data=None):
        self.d = dict(data or {})

    def get(self, k, default=None):
        return self.d.get(k, default)

    def save(self, k, v):
        self.d[k] = v


def test_gate_supervised_requires_a_pin():
    st = FakeState({'config': {}, 'pins': {'default': '1234'}})
    with patch.object(wp, '_state', return_value=st):
        # no pin -> refused, message tells the AI to ask the user
        msg = wp._gate('default', None)
        assert msg is not None and 'PIN' in msg
        # wrong pin -> refused, op must NOT proceed
        assert 'Incorrect' in wp._gate('default', '0000')
        # correct pin -> allowed (None)
        assert wp._gate('default', '1234') is None


def test_gate_unsupervised_allows_without_pin():
    st = FakeState({'config': {'unsupervised': True}})
    with patch.object(wp, '_state', return_value=st):
        assert wp._gate('default', None) is None
        assert wp._gate('default', 'anything') is None


def test_gate_mints_pin_when_supervised_and_missing():
    # Supervised site with no PIN yet: gate must still demand one (and create it),
    # never silently allow.
    st = FakeState({'config': {}, 'pins': {}})
    with patch.object(wp, '_state', return_value=st):
        msg = wp._gate('newsite', None)
        assert msg is not None and 'PIN' in msg
        assert st.get('pins', {}).get('newsite')  # a PIN now exists


def test_ensure_pin_is_4_digits_and_idempotent():
    st = FakeState({})
    with patch.object(wp, '_state', return_value=st):
        pin = wp._ensure_pin('s')
        assert pin and pin.isdigit() and len(pin) == 4
        assert wp._ensure_pin('s') == pin  # does not re-roll an existing PIN


def test_burn_regenerates_and_publishes_in_supervised_mode():
    st = FakeState({'config': {}, 'pins': {'s': '1111'}})
    with patch.object(wp, '_state', return_value=st), \
         patch.object(wp, '_gen_pin', return_value='9999'), \
         patch('core.event_bus.publish') as pub:
        wp._burn_pin('s')
        assert st.get('pins')['s'] == '9999'
        pub.assert_called_once()


def test_burn_is_noop_when_unsupervised():
    st = FakeState({'config': {'unsupervised': True}, 'pins': {'s': '1111'}})
    with patch.object(wp, '_state', return_value=st), \
         patch('core.event_bus.publish') as pub:
        wp._burn_pin('s')
        assert st.get('pins')['s'] == '1111'  # unchanged
        pub.assert_not_called()


def test_burn_is_noop_when_rotation_static():
    st = FakeState({'config': {'rotation': 'static'}, 'pins': {'s': '1111'}})
    with patch.object(wp, '_state', return_value=st), \
         patch('core.event_bus.publish') as pub:
        wp._burn_pin('s')
        assert st.get('pins')['s'] == '1111'  # static = don't rotate
        pub.assert_not_called()
