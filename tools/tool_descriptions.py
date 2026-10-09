#!/usr/bin/env python3
"""Tool descriptions out to JSON, and back in.

    python tools/tool_descriptions.py export            # writes tmp/tool-descriptions-{core,user}.json
    python tools/tool_descriptions.py import            # dry run: what would change
    python tools/tool_descriptions.py import --write    # rewrite the tool files, re-sign plugins
    python tools/tool_descriptions.py stats [--top 25]  # who is heavy (tokens ~ chars / 4)

A param set to "" loses its description (the name says it); a tool always keeps
one. Only plain string literals are editable. A description built in code (f-string,
variable, concat) is listed under "_computed" and left alone. The import swaps
the literal's exact span and refuses a file unless everything else in it parses
to the same tree.
"""
import argparse
import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETS = {
    'core': ['functions/*.py', 'plugins/*/tools/*.py'],
    'user': ['user/functions/*.py', 'user/plugins/*/tools/*.py'],
}
WIDTH = 100


def _is_str(node):
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _items(d):
    return [(k.value, v) for k, v in zip(d.keys, d.values) if _is_str(k)]


def _params(node, path, out, computed):
    """Every description under a tool's parameters, keyed by its path."""
    if isinstance(node, (ast.List, ast.Tuple)):
        for el in node.elts:
            _params(el, path, out, computed)
    if not isinstance(node, ast.Dict):
        return
    for knode, val in zip(node.keys, node.values):
        if not _is_str(knode):
            continue
        key = knode.value
        if key == 'description' and path:
            name = '.'.join(path)
            if _is_str(val):
                val._key = knode
                out[name] = val
            else:
                computed.append(f"{name} (line {val.lineno})")
        elif key == 'properties':
            _params(val, path, out, computed)
        else:
            _params(val, path + [key], out, computed)


def scan(src):
    """{tool: {'description': node|None, 'params': {path: node}}}, [computed notes]"""
    tools, computed = {}, []
    for d in ast.walk(ast.parse(src)):
        if not isinstance(d, ast.Dict):
            continue
        keys = dict(_items(d))
        if not {'name', 'description', 'parameters'} <= set(keys):
            continue
        if not _is_str(keys['name']):
            computed.append(f"tool with a computed name (line {d.lineno})")
            continue
        name, n = keys['name'].value, 2
        while name in tools:                       # same tool defined twice in one file
            name, n = f"{keys['name'].value}#{n}", n + 1
        desc = keys['description']
        if not _is_str(desc):
            computed.append(f"{name} (line {desc.lineno})")
            desc = None
        params, notes = {}, []
        _params(keys['parameters'], [], params, notes)
        computed += [f"{name}.{x}" for x in notes]
        tools[name] = {'description': desc, 'params': params}
    return tools, computed


def _files(which, root):
    return sorted(f for pat in SETS[which] for f in root.glob(pat) if not f.name.startswith('_'))


def _json_path(which, root):
    return root / 'tmp' / f'tool-descriptions-{which}.json'


def export(root):
    for which in SETS:
        doc, tools_n, chars = {}, 0, 0
        for f in _files(which, root):
            tools, computed = scan(f.read_text(encoding='utf-8'))
            if not tools and not computed:
                continue
            entry = {}
            for name, t in tools.items():
                row = {}
                if t['description'] is not None:
                    row['description'] = t['description'].value
                if t['params']:
                    row['params'] = {p: n.value for p, n in t['params'].items()}
                entry[name] = row
                chars += len(row.get('description', '')) + sum(len(v) for v in row.get('params', {}).values())
            if computed:
                entry['_computed'] = computed
            doc[str(f.relative_to(root))] = entry
            tools_n += len(tools)
        out = _json_path(which, root)
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(f"{which}: {tools_n} tools in {len(doc)} files, {chars} chars -> {out.relative_to(root)}")


def stats(root, top):
    rows = []                                  # (total, desc, params, n_params, computed, tool, file, which)
    for which in SETS:
        for f in _files(which, root):
            tools, computed = scan(f.read_text(encoding='utf-8'))
            for name, t in tools.items():
                d = len(t['description'].value) if t['description'] is not None else 0
                ps = sum(len(n.value) for n in t['params'].values())
                c = sum(1 for x in computed if x.split(' ')[0].split('.')[0] == name)
                rows.append((d + ps, d, ps, len(t['params']), c, name, str(f.relative_to(root)), which))
    if not rows:
        return print("no tools found")
    for which in SETS:
        mine = sorted(r[0] for r in rows if r[7] == which)
        if not mine:
            continue
        tot, n = sum(mine), len(mine)
        fat = [x for x in mine if x > 400]
        print(f"{which}: {n} tools, {tot} chars (~{tot // 4} tokens) | median {mine[n // 2]}, mean {tot // n}, "
              f"max {mine[-1]} | over 400 chars: {len(fat)} tools hold {sum(fat) * 100 // tot}% of the weight | "
              f"descriptions {sum(r[1] for r in rows if r[7] == which)}, params {sum(r[2] for r in rows if r[7] == which)}")
    print(f"\n{'chars':>6} {'~tok':>5} {'desc':>5} {'params':>6} {'#p':>3}  tool")
    for tot, d, ps, n, c, name, f, _ in sorted(rows, reverse=True)[:top]:
        print(f"{tot:>6} {tot // 4:>5} {d:>5} {ps:>6} {n:>3}  {name}  ({f}){f'  +{c} computed, not counted' if c else ''}")
    by_file = {}
    for r in rows:
        by_file[r[6]] = by_file.get(r[6], 0) + r[0]
    print(f"\n{'chars':>6}  file")
    for f, tot in sorted(by_file.items(), key=lambda x: -x[1])[:12]:
        print(f"{tot:>6}  {f}")


def _literal(text, col):
    """A Python string literal for text; long ones wrap as adjacent strings."""
    if len(text) <= WIDTH or '\n' in text:
        return json.dumps(text, ensure_ascii=False)
    parts = textwrap.wrap(text, WIDTH, drop_whitespace=False, break_long_words=False, break_on_hyphens=False)
    return ('\n' + ' ' * col).join(json.dumps(p, ensure_ascii=False) for p in parts)


def _blank(src, drop=()):
    """The file's tree with every editable description emptied (and the dropped
    ones gone): what a rewrite must NOT change."""
    tree = ast.parse(src)
    # scan() walks its own parse; positions match, so find them by position here
    spots = {(n.lineno, n.col_offset) for t in scan(src)[0].values()
             for n in [t['description'], *t['params'].values()] if n is not None}
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict) and drop:
            keep = [i for i, v in enumerate(n.values) if (v.lineno, v.col_offset) not in drop]
            n.keys, n.values = [n.keys[i] for i in keep], [n.values[i] for i in keep]
    for n in ast.walk(tree):
        if _is_str(n) and (n.lineno, n.col_offset) in spots:
            n.value = ''
    return ast.dump(tree)


def _cut_span(raw, key_end, val_start, end):
    """Where a `"description": "..."` pair ends once its parentheses, its comma
    and (when it had the line to itself) its line go with it. Start None = the
    key's own start; a last key gives back the comma before it instead."""
    i = end
    for _ in range(raw[key_end:val_start].count(b'(')):        # ("a" "b") - the node stops inside
        while raw[i:i + 1] in (b' ', b'\t', b'\n', b'\r'):
            i += 1
        if raw[i:i + 1] != b')':
            raise ValueError("unbalanced parentheses round a description")
        i += 1
    start = raw.rfind(b'"description"', 0, key_end)
    if start < 0:
        start = raw.rfind(b"'description'", 0, key_end)
    while raw[i:i + 1] in (b' ', b'\t'):
        i += 1
    if raw[i:i + 1] == b',':
        i += 1
        while raw[i:i + 1] in (b' ', b'\t'):
            i += 1
        line = raw.rfind(b'\n', 0, start) + 1
        if raw[i:i + 1] in (b'\n', b'\r') and not raw[line:start].strip():
            return line, raw.index(b'\n', i) + 1
        return start, i
    j = start
    while raw[j - 1:j] in (b' ', b'\t', b'\n', b'\r'):
        j -= 1
    return (j - 1, i) if raw[j - 1:j] == b',' else (start, i)


def rewrite(src, want):
    """(new source, [changes]). want = this file's JSON entry. A param set to ""
    loses its description key - the name says it."""
    tools, _ = scan(src)
    edits, changes = [], []
    for name, row in want.items():
        if name == '_computed':
            continue
        if name not in tools:
            raise ValueError(f"no tool named {name!r} here (renamed since the export?)")
        pairs = [(None, tools[name]['description'], row.get('description'))]
        for p, text in (row.get('params') or {}).items():
            if p not in tools[name]['params']:
                if text == '':
                    continue                                   # already dropped
                raise ValueError(f"{name}: no param {p!r} here")
            pairs.append((p, tools[name]['params'][p], text))
        for label, node, text in pairs:
            if node is None or text is None or text == node.value:
                continue
            if not isinstance(text, str):
                raise ValueError(f"{name}.{label}: not a string")
            if label is None and not text.strip():
                raise ValueError(f"{name}: a tool keeps a description (only params may go blank)")
            edits.append((node, text))
            changes.append((name if label is None else f"{name}.{label}", len(node.value), len(text)))
    if not edits:
        return src, []
    raw = src.encode('utf-8')                                  # ast columns are utf-8 bytes
    starts = [0]
    for line in raw.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    at = lambda line, col: starts[line - 1] + col
    for node, text in sorted(edits, key=lambda e: (e[0].lineno, e[0].col_offset), reverse=True):
        a, b = at(node.lineno, node.col_offset), at(node.end_lineno, node.end_col_offset)
        if text == '':
            a, b = _cut_span(raw, at(node._key.lineno, node._key.end_col_offset), a, b)
            a = at(node._key.lineno, node._key.col_offset) if a is None else a
            raw = raw[:a] + raw[b:]
        else:
            col = len(raw[raw.rfind(b'\n', 0, a) + 1:a].decode('utf-8'))
            raw = raw[:a] + _literal(text, col).encode('utf-8') + raw[b:]
    new = raw.decode('utf-8')
    drop = {(n.lineno, n.col_offset) for n, text in edits if text == ''}
    if _blank(new) != _blank(src, drop):
        raise ValueError("the rewrite changed more than descriptions")
    got = scan(new)[0]
    for name, row in want.items():
        if name == '_computed':
            continue
        if row.get('description') is not None and got[name]['description'] is not None:
            assert got[name]['description'].value == row['description'], name
        for p, text in (row.get('params') or {}).items():
            assert (p not in got[name]['params']) if text == '' else (got[name]['params'][p].value == text), (name, p)
    return new, changes


def _plugin_dir(rel):
    parts = Path(rel).parts
    i = parts.index('plugins') if 'plugins' in parts else -1
    return Path(*parts[:i + 2]) if i >= 0 else None


def load(root, write):
    touched, failed, before, after = set(), [], 0, 0
    for which in SETS:
        path = _json_path(which, root)
        if not path.exists():
            print(f"{which}: no {path.relative_to(root)}, skipped")
            continue
        for rel, want in json.loads(path.read_text(encoding='utf-8')).items():
            f = root / rel
            try:
                with open(f, encoding='utf-8', newline='') as fh:
                    src = fh.read()
                new, changes = rewrite(src, want)
            except Exception as e:
                failed.append(rel)
                print(f"SKIPPED {rel}: {e}")
                continue
            if not changes:
                continue
            print(f"{rel}")
            for label, old, cur in changes:
                print(f"    {label}: {old} -> {cur} chars")
                before, after = before + old, after + cur
            if write:
                tmp = f.with_suffix('.py.tmp')
                with open(tmp, 'w', encoding='utf-8', newline='') as fh:
                    fh.write(new)
                tmp.replace(f)
                if _plugin_dir(rel):
                    touched.add(_plugin_dir(rel))
    print(f"\n{'wrote' if write else 'would change'}: {before} -> {after} chars"
          + (f" ({100 - after * 100 // before}% smaller)" if before else ''))
    for plug in sorted(touched):
        if not (root / plug / 'plugin.sig').exists():
            continue                                    # unsigned plugin: nothing to re-sign
        r = subprocess.run([sys.executable, str(root / 'tools' / 'sign_plugin.py'), str(plug)],
                           cwd=root, capture_output=True, text=True)
        print(f"re-signed {plug}" if r.returncode == 0 else f"SIGN FAILED {plug}: {r.stderr.strip() or r.stdout.strip()}")
        if r.returncode:
            failed.append(str(plug))
    if not write and before != after:
        print("dry run - add --write to apply")
    return 1 if failed else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('action', choices=['export', 'import', 'stats'])
    ap.add_argument('--write', action='store_true', help='import: rewrite the files (default is a dry run)')
    ap.add_argument('--top', type=int, default=25, help='stats: how many offenders to list')
    ap.add_argument('--root', default=str(ROOT), help=argparse.SUPPRESS)
    args = ap.parse_args()
    root = Path(args.root).absolute()
    if args.action == 'stats':
        sys.exit(stats(root, args.top) or 0)
    sys.exit(export(root) or 0 if args.action == 'export' else load(root, args.write))
