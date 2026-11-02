# tools/memory_remove.py - prune Memory (legacy) rows by phrase. The DBs are
# built by the classic plugin's own _ensure_db so the FTS triggers are the real
# ones; the script runs as a subprocess, the way a person runs it.
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "memory_remove.py"


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    from plugins.memory.tools import goals_tools, knowledge_tools, memory_tools
    for mod, name in ((memory_tools, 'memory.db'), (knowledge_tools, 'knowledge.db'),
                      (goals_tools, 'goals.db')):
        monkeypatch.setattr(mod, '_db_path', tmp_path / name)
        monkeypatch.setattr(mod, '_db_initialized', False)
        mod._ensure_db()
    with sqlite3.connect(tmp_path / 'memory.db') as c:
        c.executemany('INSERT INTO memories (content, scope, timestamp, importance, keywords) VALUES (?, ?, ?, 5, ?)', [
            ('Krem keeps the boat plans in the garage', 'default', '2026-01-01 10:00:00', 'boat garage'),
            ('The GARAGE roof leaks when it rains', 'work', '2026-01-02 10:00:00', 'roof'),
            ('Coffee at seven, always', 'default', '2026-01-03 10:00:00', 'coffee'),
            ('Nothing here', 'default', '2026-01-04 10:00:00', None),
        ])
    with sqlite3.connect(tmp_path / 'knowledge.db') as c:
        c.executemany('INSERT INTO people (name, relationship, notes, scope) VALUES (?, ?, ?, ?)', [
            ('Alice', 'neighbor', 'lives past the garage', 'default'),
            ('Bob', 'friend', 'plays chess', 'default'),
        ])
        c.executemany('INSERT INTO knowledge_tabs (id, name, description, type, scope) VALUES (?, ?, ?, ?, ?)', [
            (1, 'Garage inventory', 'tools on the bench', 'notes', 'default'),
            (2, 'Recipes', 'food', 'notes', 'default'),
        ])
        c.executemany('INSERT INTO knowledge_entries (tab_id, content, chunk_index, source_filename) VALUES (?, ?, ?, ?)', [
            (1, 'hammer, saw, plane', 0, None),
            (1, 'two old cars', 1, None),
            (2, 'bread: flour water salt', 0, 'bread.txt'),
            (2, 'the garage fridge holds the dough', 1, 'bread.txt'),
        ])
    with sqlite3.connect(tmp_path / 'goals.db') as c:
        c.executemany('INSERT INTO goals (id, title, description, priority, status, parent_id, scope) VALUES (?, ?, ?, ?, ?, ?, ?)', [
            (1, 'Clear the garage', None, 'medium', 'active', None, 'default'),
            (2, 'Sort the shelf', None, 'medium', 'active', 1, 'default'),
            (3, 'Learn piano', None, 'low', 'active', None, 'default'),
        ])
        c.executemany('INSERT INTO goal_progress (goal_id, note) VALUES (?, ?)', [
            (2, 'half the shelf done'), (3, 'scales in the garage, loud'),
        ])
    return tmp_path


def run(user_dir, *args, answers=''):
    r = subprocess.run([sys.executable, str(TOOL), *args, '--user-dir', str(user_dir)],
                       input=answers, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout


def count(user_dir, db, sql):
    with sqlite3.connect(user_dir / db) as c:
        return c.execute(sql).fetchone()[0]


def test_count_prints_numbers_and_deletes_nothing(user_dir):
    code, out = run(user_dir, 'garage', '--count')
    assert code == 0
    lines = {l.split()[0] if not l.startswith('knowledge tabs') else 'tabs': l for l in out.splitlines()}
    assert lines['memories'].endswith(' 2')
    assert lines['people'].endswith(' 1')
    assert lines['tabs'].endswith(' 1')
    assert lines['knowledge'].endswith(' 1')
    assert lines['goals'].endswith(' 1')
    assert lines['goal'].endswith(' 1')       # "goal notes"
    assert lines['total'].endswith(' 7')
    assert 'boat plans' not in out            # count mode shows no text
    assert count(user_dir, 'memory.db', 'SELECT COUNT(*) FROM memories') == 4


def test_yes_and_no_per_match_and_fts_follows(user_dir):
    code, out = run(user_dir, 'garage', answers='y\nn\nq\n')
    assert code == 0
    assert '[memory #1]' in out and '[memory #2]' in out
    assert 'stopped early' in out
    with sqlite3.connect(user_dir / 'memory.db') as c:
        assert [r[0] for r in c.execute('SELECT id FROM memories ORDER BY id')] == [2, 3, 4]
        assert c.execute("SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH 'garage'").fetchone()[0] == 1
        c.execute("INSERT INTO memories_fts(memories_fts) VALUES ('integrity-check')")
    # q stopped before the other stores
    assert count(user_dir, 'knowledge.db', 'SELECT COUNT(*) FROM people') == 2


def test_yes_flag_sweeps_every_store_with_cascades(user_dir):
    code, out = run(user_dir, 'garage', '--yes')
    assert code == 0
    assert '7 matched, 7 deleted' in out
    assert count(user_dir, 'memory.db', 'SELECT COUNT(*) FROM memories') == 2
    assert count(user_dir, 'knowledge.db', "SELECT COUNT(*) FROM people") == 1
    assert count(user_dir, 'knowledge.db', "SELECT COUNT(*) FROM knowledge_tabs") == 1
    # tab 1 went with both entries; tab 2 lost only the matching chunk
    assert count(user_dir, 'knowledge.db', "SELECT COUNT(*) FROM knowledge_entries") == 1
    assert count(user_dir, 'knowledge.db', "SELECT COUNT(*) FROM knowledge_fts WHERE knowledge_fts MATCH 'garage'") == 0
    assert count(user_dir, 'knowledge.db', "SELECT COUNT(*) FROM knowledge_fts WHERE knowledge_fts MATCH 'bread'") == 1
    # goal 1 took its subtask and the subtask's note; goal 3 kept the goal, lost the note
    assert [r for r in sqlite3.connect(user_dir / 'goals.db').execute('SELECT id FROM goals ORDER BY id')] == [(3,)]
    assert count(user_dir, 'goals.db', 'SELECT COUNT(*) FROM goal_progress') == 0


def test_all_answer_and_several_phrases(user_dir):
    # 'a' covers the rest of ONE store; the next store asks again
    code, out = run(user_dir, 'coffee', 'chess', answers='a\ny\n')
    assert code == 0
    assert out.count('delete? [y/n/a/q]') == 2
    assert '2 matched, 2 deleted' in out
    assert count(user_dir, 'memory.db', "SELECT COUNT(*) FROM memories WHERE content LIKE '%Coffee%'") == 0
    assert count(user_dir, 'knowledge.db', "SELECT COUNT(*) FROM people WHERE name = 'Bob'") == 0


def test_scope_filter_leaves_other_scopes_alone(user_dir):
    code, out = run(user_dir, 'garage', '--scope', 'work', '--count')
    assert code == 0
    assert out.splitlines()[-1].endswith(' 1')      # only the work-scope memory
    code, out = run(user_dir, 'garage', '--scope', 'work', '--yes')
    assert '1 matched, 1 deleted' in out
    assert count(user_dir, 'memory.db', "SELECT COUNT(*) FROM memories WHERE scope = 'default'") == 3


def test_missing_store_is_skipped(user_dir):
    (user_dir / 'goals.db').unlink()
    code, out = run(user_dir, 'garage', '--count')
    assert code == 0
    assert 'goals.db: not here' in out
    assert out.splitlines()[-1].endswith(' 5')


def test_empty_phrase_refused(user_dir):
    code, _ = run(user_dir, '   ')
    assert code == 2
