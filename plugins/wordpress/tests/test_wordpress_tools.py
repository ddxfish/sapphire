"""Tool-level behavior for the WordPress plugin: category resolution, the
protected-settings guard, trash-vs-permanent delete gating, and small helpers.
All mocked - no live WordPress.
"""
from unittest.mock import patch

import plugins.wordpress.tools.wordpress as wp
import plugins.wordpress.routes.manage as mg


class FakeState:
    def __init__(self, data=None):
        self.d = dict(data or {})

    def get(self, k, default=None):
        return self.d.get(k, default)

    def save(self, k, v):
        self.d[k] = v


# --- category resolution ---

def test_resolve_category_numeric_passthrough():
    assert wp._resolve_category('s', '7') == (7, None)


def test_resolve_category_by_name_and_slug_case_insensitive():
    cats = [{'id': 3, 'name': 'News', 'slug': 'news'},
            {'id': 5, 'name': 'Tutorials', 'slug': 'tutorials'}]
    with patch.object(wp, '_wp_request', return_value=(cats, None)):
        assert wp._resolve_category('s', 'news')[0] == 3       # name, lowercased
        assert wp._resolve_category('s', 'TUTORIALS')[0] == 5  # name, uppercased
        assert wp._resolve_category('s', 'tutorials')[0] == 5  # slug


def test_resolve_category_not_found_lists_available():
    cats = [{'id': 3, 'name': 'News', 'slug': 'news'}]
    with patch.object(wp, '_wp_request', return_value=(cats, None)):
        cid, err = wp._resolve_category('s', 'Sports')
        assert cid is None
        assert 'not found' in err.lower() and 'News' in err


# --- protected settings guard ---

def test_settings_write_refuses_protected_keys_without_touching_wp():
    st = FakeState({'config': {}})
    for key in ('url', 'home', 'siteurl', 'email', 'admin_email'):
        with patch.object(wp, '_get_scope', return_value='default'), \
             patch.object(wp, '_state', return_value=st), \
             patch.object(wp, '_wp_request') as req:
            msg, ok = wp.execute('wp_settings',
                                 {'name': key, 'value': 'x', 'pin': '1234'}, None)
            assert ok is False and 'protected' in msg.lower()
            req.assert_not_called()  # never reaches WordPress, even with a valid PIN


# --- delete gating: trash is free, permanent needs the PIN ---

def test_delete_blog_trash_needs_no_pin():
    st = FakeState({'config': {}, 'pins': {'default': '1234'}})
    with patch.object(wp, '_get_scope', return_value='default'), \
         patch.object(wp, '_state', return_value=st), \
         patch.object(wp, '_wp_request', return_value=(True, None)) as req:
        msg, ok = wp.execute('wp_delete_blog', {'id': '5'}, None)  # no force
        assert ok is True and 'Trash' in msg
        _, kwargs = req.call_args
        assert kwargs.get('params') is None  # no force flag sent


def test_delete_blog_permanent_requires_pin_before_calling_wp():
    st = FakeState({'config': {}, 'pins': {'default': '1234'}})
    with patch.object(wp, '_get_scope', return_value='default'), \
         patch.object(wp, '_state', return_value=st), \
         patch.object(wp, '_wp_request') as req:
        msg, ok = wp.execute('wp_delete_blog', {'id': '5', 'force': True}, None)  # no pin
        assert ok is False and 'PIN' in msg
        req.assert_not_called()  # gated before any destructive call


def test_delete_blog_permanent_with_correct_pin_deletes_and_burns():
    st = FakeState({'config': {}, 'pins': {'default': '1234'}})
    with patch.object(wp, '_get_scope', return_value='default'), \
         patch.object(wp, '_state', return_value=st), \
         patch.object(wp, '_gen_pin', return_value='9999'), \
         patch('core.event_bus.publish'), \
         patch.object(wp, '_wp_request', return_value=(True, None)) as req:
        msg, ok = wp.execute('wp_delete_blog',
                             {'id': '5', 'force': True, 'pin': '1234'}, None)
        assert ok is True and 'Permanently deleted' in msg
        assert st.get('pins')['default'] == '9999'   # PIN burned
        _, kwargs = req.call_args
        assert (kwargs.get('params') or {}).get('force') == 'true'


def test_delete_user_requires_pin():
    st = FakeState({'config': {}, 'pins': {'default': '1234'}})
    with patch.object(wp, '_get_scope', return_value='default'), \
         patch.object(wp, '_state', return_value=st), \
         patch.object(wp, '_wp_request') as req:
        msg, ok = wp.execute('wp_delete_user', {'id': '8'}, None)  # no pin
        assert ok is False and 'PIN' in msg
        req.assert_not_called()


# --- helpers ---

def test_pg_clamps_to_valid_page_numbers():
    assert wp._pg(0) == 1
    assert wp._pg(-3) == 1
    assert wp._pg('4') == 4
    assert wp._pg('abc') == 1
    assert wp._pg(None) == 1
    assert wp._pg(2) == 2


def test_sanitize_scope_strips_unsafe_chars():
    assert mg._sanitize_scope('My Site') == 'my-site'
    assert mg._sanitize_scope('../../etc') == 'etc'   # path-traversal chars gone
    assert mg._sanitize_scope('Blog_2') == 'blog_2'
    assert mg._sanitize_scope('  Spaces  ') == 'spaces'
    assert mg._sanitize_scope('') == ''
    assert len(mg._sanitize_scope('a' * 100)) <= 40
