"""Entity kind templates + nickname aliases (2026-07-11 additions).

Templates: defaults ship in the plugin's templates/ dir; user kinds load from
user/mind_palace/templates and win on collision. Aliases: entity_aliases folds
meta.fields.nicknames (CSV) into the match set so 'the boss' resolves to Krem
at save-time seeding, backfill, and spider epicenter resolution.
"""
import json
import sqlite3

from plugins.mindpalace.tools import metadata as md
from plugins.mindpalace.tools import templates as tpl


def _db():
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT, "
                 "scope TEXT, meta TEXT)")
    conn.execute("INSERT INTO entities VALUES (1, 'Krem', 'default', ?)",
                 (json.dumps({'fields': {'nicknames': 'the boss, kremster'}}),))
    conn.execute("INSERT INTO entities VALUES (2, 'Sapphire', 'default', NULL)")
    conn.execute("INSERT INTO entities VALUES (3, 'Falcon', 'global', NULL)")
    conn.execute("INSERT INTO entities VALUES (4, 'Tom', 'work', NULL)")
    return conn


def test_entity_aliases_includes_nicknames_and_overlay():
    rows = md.entity_aliases(_db().cursor(), 'default')
    pairs = {(i, a) for i, _, a in rows}
    assert (1, 'Krem') in pairs
    assert (1, 'the boss') in pairs
    assert (1, 'kremster') in pairs
    assert (2, 'Sapphire') in pairs
    assert (3, 'Falcon') in pairs          # global overlay included
    assert all(i != 4 for i, _ in pairs)  # other scope excluded


def test_nickname_matches_through_match_entities():
    rows = md.entity_aliases(_db().cursor(), 'default')
    amap = md.alias_map(rows)
    hits = md.match_entities("Talked to THE BOSS about the boat",
                             [a for _, _, a in rows])
    assert any(amap[h.lower()][0] == 1 for h in hits)
    assert amap['the boss'][1] == 'Krem'   # canonical name recoverable


def test_alias_map_real_name_beats_nickname():
    rows = [(1, 'Krem', 'Krem'), (1, 'Krem', 'Sapphire'),
            (2, 'Sapphire', 'Sapphire')]
    amap = md.alias_map(rows)
    assert amap['sapphire'] == (2, 'Sapphire')


def _write_tpl(d, name, obj):
    (d / name).write_text(json.dumps(obj), encoding='utf-8')


def _fresh(monkeypatch, defaults_dir, user_dir):
    monkeypatch.setattr(tpl, '_DEFAULTS_DIR', defaults_dir)
    monkeypatch.setattr(tpl, '_user_dir', lambda: user_dir)
    monkeypatch.setattr(tpl, '_cache', None)
    monkeypatch.setattr(tpl, '_cache_stamp', None)


def test_user_template_overrides_default(tmp_path, monkeypatch):
    dflt, user = tmp_path / 'd', tmp_path / 'u'
    dflt.mkdir(); user.mkdir()
    _write_tpl(dflt, 'person.json', {'kind': 'person', 'label': 'Person',
                                     'fields': [{'key': 'phone'}]})
    _write_tpl(user, 'person.json', {'kind': 'person', 'label': 'Custom Person',
                                     'fields': []})
    _write_tpl(user, 'dragon.json', {'kind': 'dragon', 'label': 'Dragon', 'order': 5,
                                     'fields': [{'key': 'hoard', 'type': 'bool'}]})
    _fresh(monkeypatch, dflt, user)
    ts = tpl.get_templates()
    assert ts['person']['label'] == 'Custom Person'
    assert 'dragon' in ts and ts['dragon']['fields'][0]['type'] == 'bool'
    assert tpl.valid_kinds() == {'person', 'dragon'}


def test_invalid_templates_skipped_and_types_coerced(tmp_path, monkeypatch):
    dflt, user = tmp_path / 'd', tmp_path / 'u'
    dflt.mkdir(); user.mkdir()
    _write_tpl(dflt, 'nokind.json', {'label': 'No Kind'})
    (dflt / 'broken.json').write_text('{not json', encoding='utf-8')
    _write_tpl(dflt, 'ok.json', {'kind': 'Event!', 'fields': [
        {'key': 'date', 'type': 'datetime'},   # unknown type → text
        {'key': ''},                            # empty key dropped
    ]})
    _fresh(monkeypatch, dflt, user)
    ts = tpl.get_templates()
    assert set(ts) == {'event'}                 # kind slugified, junk skipped
    assert ts['event']['fields'] == [
        {'key': 'date', 'label': 'date', 'type': 'text', 'default': None}]


def test_cache_invalidates_on_new_user_file(tmp_path, monkeypatch):
    dflt, user = tmp_path / 'd', tmp_path / 'u'
    dflt.mkdir(); user.mkdir()
    _write_tpl(dflt, 'thing.json', {'kind': 'thing'})
    _fresh(monkeypatch, dflt, user)
    assert set(tpl.get_templates()) == {'thing'}
    _write_tpl(user, 'boat.json', {'kind': 'boat'})
    assert set(tpl.get_templates()) == {'thing', 'boat'}


def test_textarea_field_type_ships_on_person():
    t = tpl.get_templates()['person']
    by_key = {f['key']: f for f in t['fields']}
    assert by_key['background']['type'] == 'textarea'
    assert by_key['voice']['type'] == 'textarea'
    assert by_key['likes']['type'] == 'textarea'
    assert by_key['dislikes']['type'] == 'textarea'
    assert by_key['relationship']['type'] == 'text'      # untouched
