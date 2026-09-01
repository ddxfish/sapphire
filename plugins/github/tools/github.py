# GitHub plugin tools — repos, files, issues, search.
# Pure REST via requests. PAT-authenticated, scope-aware multi-account.

import base64
import fnmatch
import json
import logging
from pathlib import Path
from typing import Optional

import requests
from core import net

logger = logging.getLogger(__name__)

ENABLED = True
EMOJI = '🐙'
API_BASE = 'https://api.github.com'

AVAILABLE_FUNCTIONS = ['github_repo', 'github_file', 'github_issue', 'github_search']

TOOLS = [
    {
        "type": "function",
        "is_local": False,
        "network": True,
        "function": {
            "name": "github_repo",
            "description": (
                "Manage GitHub repositories on the active scope's account. "
                "Actions: 'create' (name, private?, description?), "
                "'list' (lists your own repos), "
                "'get' (repo='owner/name' or just 'name' for your own), "
                "'delete' (repo, requires DELETE permission), "
                "'fork' (repo='upstream/name')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "list", "get", "delete", "fork"]},
                    "name": {"type": "string", "description": "Repo name (for create)"},
                    "repo": {"type": "string", "description": "Repo as 'owner/name' or just 'name' for your own (for get/delete/fork)"},
                    "private": {"type": "boolean", "description": "Private repo (for create). Default false."},
                    "description": {"type": "string", "description": "Repo description (for create)"},
                    "auto_init": {"type": "boolean", "description": "Initialize with README (for create). Default false — leave empty so push_directory makes the first commit."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "is_local": False,
        "network": True,
        "function": {
            "name": "github_file",
            "description": (
                "Read, write, or delete files in a GitHub repo. "
                "Actions: 'read' (repo, path, ref?), "
                "'write' (repo, path, content, commit_message, branch?), "
                "'delete' (repo, path, commit_message, branch?), "
                "'push_directory' (repo, local_path, commit_message, branch?, exclude?) — bulk push a local directory as ONE commit via the git tree API."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "write", "delete", "push_directory"]},
                    "repo": {"type": "string", "description": "Repo as 'owner/name'"},
                    "path": {"type": "string", "description": "File path inside the repo (for read/write/delete)"},
                    "content": {"type": "string", "description": "File content as text (for write)"},
                    "commit_message": {"type": "string", "description": "Commit message (for write/delete/push_directory)"},
                    "branch": {"type": "string", "description": "Branch to commit to. Default: repo's default branch."},
                    "ref": {"type": "string", "description": "Branch/tag/commit to read from (for read). Default: default branch."},
                    "local_path": {"type": "string", "description": "Local directory to push (for push_directory). Path inside Sapphire's working tree."},
                    "exclude": {"type": "array", "items": {"type": "string"}, "description": "Glob patterns to exclude (for push_directory). e.g. ['__pycache__', '*.pyc']."}
                },
                "required": ["action", "repo"]
            }
        }
    },
    {
        "type": "function",
        "is_local": False,
        "network": True,
        "function": {
            "name": "github_issue",
            "description": (
                "Manage issues in a GitHub repo. Filing issues uses the active scope's identity — "
                "if you are running as your own GitHub account, the issue is authored by you. "
                "Actions: 'create' (repo, title, body?), "
                "'list' (repo, state?='open'|'closed'|'all'), "
                "'get' (repo, number), "
                "'comment' (repo, number, body), "
                "'close' (repo, number)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "list", "get", "comment", "close"]},
                    "repo": {"type": "string", "description": "Repo as 'owner/name'"},
                    "number": {"type": "integer", "description": "Issue number (for get/comment/close)"},
                    "title": {"type": "string", "description": "Issue title (for create)"},
                    "body": {"type": "string", "description": "Issue body or comment text (for create/comment)"},
                    "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "Filter (for list). Default 'open'."}
                },
                "required": ["action", "repo"]
            }
        }
    },
    {
        "type": "function",
        "is_local": False,
        "network": True,
        "function": {
            "name": "github_search",
            "description": "Search GitHub. Types: 'repos', 'code', 'issues'. Returns up to 'limit' results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["repos", "code", "issues"]},
                    "query": {"type": "string", "description": "Search query (GitHub search syntax)"},
                    "limit": {"type": "integer", "description": "Max results. Default 10, max 30."}
                },
                "required": ["type", "query"]
            }
        }
    },
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_github_scope() -> Optional[str]:
    """Resolve the active github scope. Returns None when scope is unset/disabled.

    Returning 'default' on unset is a silent-default class regression: a user
    who picks 'none' in the sidebar dropdown (chat-level disable) gets their
    tool calls silently routed to the default account. Mirroring the email
    plugin's pattern — return None and fail closed in the caller. 2026-05-14.
    """
    try:
        from core.chat.function_manager import SCOPE_REGISTRY
        reg = SCOPE_REGISTRY.get('github')
        if reg:
            val = reg['var'].get()
            return val if val else None
    except Exception as e:
        logger.debug(f"github: scope resolution failed: {e}")
    return None


def _get_github_creds():
    """Load the active scope's github credentials. Returns (username, pat, error_str_or_None)."""
    from core.credentials_manager import credentials
    scope = _get_github_scope()
    if scope is None:
        return '', '', "GitHub is disabled for this chat."
    acct = credentials.get_github_account(scope)
    if not acct.get('pat'):
        return '', '', f"No GitHub PAT for scope '{scope}'. Add one in Settings > Plugins > GitHub."
    return acct['username'], acct['pat'], None


def _headers(pat: str) -> dict:
    return {
        'Authorization': f'Bearer {pat}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'Sapphire-AI-github-plugin',
    }


def _api(method: str, path: str, pat: str, params=None, body=None, raw=False):
    """Call api.github.com. Returns (data, error_str_or_None).
    If raw=True, returns the raw response text (used for file content reads)."""
    url = f"{API_BASE}{path}"
    try:
        resp = net.request(
            method, url,
            headers=_headers(pat),
            params=params,
            json=body if body is not None else None,
            timeout=30,
        )
    except requests.RequestException as e:
        return None, f"Network error: {e}"

    if resp.status_code == 401:
        return None, "GitHub PAT is invalid or expired. Re-paste in Settings > Plugins > GitHub."
    if resp.status_code == 403:
        # Rate limit or insufficient permissions
        if 'rate limit' in resp.text.lower():
            return None, f"GitHub rate limit hit. Try again in a few minutes."
        return None, f"Permission denied. Token lacks required scopes for this action ({resp.status_code}): {resp.text[:200]}"
    if resp.status_code == 404:
        return None, f"Not found: {path}"
    if resp.status_code >= 400:
        try:
            err = resp.json().get('message', resp.text)
        except Exception:
            err = resp.text
        return None, f"GitHub API error {resp.status_code}: {err[:300]}"

    if raw:
        return resp.text, None
    if not resp.text:
        return {}, None
    try:
        return resp.json(), None
    except Exception:
        return resp.text, None


def _resolve_repo(repo: str, username: str) -> str:
    """Allow 'name' (your own) or 'owner/name'. Returns 'owner/name' form."""
    if '/' not in repo:
        return f"{username}/{repo}"
    return repo


# ─── github_repo actions ──────────────────────────────────────────────────────

def _repo_create(args, username, pat):
    name = args.get('name', '').strip()
    if not name:
        return "name is required for create", False
    body = {
        'name': name,
        'private': bool(args.get('private', False)),
        'description': args.get('description', ''),
        'auto_init': bool(args.get('auto_init', False)),
    }
    data, err = _api('POST', '/user/repos', pat, body=body)
    if err:
        return err, False
    return f"Created {data.get('full_name')} → {data.get('html_url')}", True


def _repo_list(args, username, pat):
    data, err = _api('GET', '/user/repos', pat, params={'per_page': 50, 'sort': 'updated'})
    if err:
        return err, False
    if not data:
        return "No repos.", True
    lines = [f"- {r['full_name']} ({'private' if r.get('private') else 'public'}) — {r.get('description') or 'no description'}" for r in data]
    return "Your repos:\n" + "\n".join(lines), True


def _repo_get(args, username, pat):
    repo = args.get('repo', '').strip()
    if not repo:
        return "repo is required for get", False
    full = _resolve_repo(repo, username)
    data, err = _api('GET', f'/repos/{full}', pat)
    if err:
        return err, False
    return (
        f"{data['full_name']} ({'private' if data.get('private') else 'public'})\n"
        f"  description: {data.get('description') or '(none)'}\n"
        f"  default branch: {data.get('default_branch')}\n"
        f"  stars: {data.get('stargazers_count', 0)}, forks: {data.get('forks_count', 0)}, open issues: {data.get('open_issues_count', 0)}\n"
        f"  url: {data.get('html_url')}"
    ), True


def _repo_delete(args, username, pat):
    repo = args.get('repo', '').strip()
    if not repo:
        return "repo is required for delete", False
    full = _resolve_repo(repo, username)
    _, err = _api('DELETE', f'/repos/{full}', pat)
    if err:
        return err, False
    return f"Deleted {full}.", True


def _repo_fork(args, username, pat):
    repo = args.get('repo', '').strip()
    if not repo or '/' not in repo:
        return "repo as 'upstream-owner/name' is required for fork", False
    data, err = _api('POST', f'/repos/{repo}/forks', pat)
    if err:
        return err, False
    return f"Forked {repo} → {data.get('full_name')} ({data.get('html_url')})", True


# ─── github_file actions ──────────────────────────────────────────────────────

def _file_read(args, username, pat):
    repo = args.get('repo', '').strip()
    path = args.get('path', '').strip()
    if not repo or not path:
        return "repo and path are required for read", False
    full = _resolve_repo(repo, username)
    params = {}
    if args.get('ref'):
        params['ref'] = args['ref']
    data, err = _api('GET', f'/repos/{full}/contents/{path}', pat, params=params)
    if err:
        return err, False
    if isinstance(data, list):
        listing = "\n".join(f"  {item['type']:4} {item['name']}" for item in data)
        return f"{full}/{path} (directory):\n{listing}", True
    if data.get('encoding') == 'base64':
        try:
            content = base64.b64decode(data['content']).decode('utf-8')
        except UnicodeDecodeError:
            return f"{full}/{path} is binary ({data.get('size', 0)} bytes) — not displayed", True
        return f"{full}/{path}:\n{content}", True
    return f"{full}/{path}: (unrecognized content encoding)", False


def _file_write(args, username, pat):
    repo = args.get('repo', '').strip()
    path = args.get('path', '').strip()
    content = args.get('content', '')
    msg = args.get('commit_message', '').strip() or f"Update {path}"
    branch = args.get('branch', '').strip() or None
    if not repo or not path:
        return "repo and path are required for write", False
    full = _resolve_repo(repo, username)

    body = {
        'message': msg,
        'content': base64.b64encode(content.encode('utf-8')).decode('ascii'),
    }
    if branch:
        body['branch'] = branch

    # If the file already exists, we need its SHA to update it.
    params = {'ref': branch} if branch else None
    existing, _ = _api('GET', f'/repos/{full}/contents/{path}', pat, params=params)
    if isinstance(existing, dict) and existing.get('sha'):
        body['sha'] = existing['sha']

    data, err = _api('PUT', f'/repos/{full}/contents/{path}', pat, body=body)
    if err:
        return err, False
    commit_url = (data.get('commit') or {}).get('html_url', '')
    return f"Wrote {full}/{path} → {commit_url}", True


def _file_delete(args, username, pat):
    repo = args.get('repo', '').strip()
    path = args.get('path', '').strip()
    msg = args.get('commit_message', '').strip() or f"Delete {path}"
    branch = args.get('branch', '').strip() or None
    if not repo or not path:
        return "repo and path are required for delete", False
    full = _resolve_repo(repo, username)

    params = {'ref': branch} if branch else None
    existing, err = _api('GET', f'/repos/{full}/contents/{path}', pat, params=params)
    if err:
        return err, False
    if not isinstance(existing, dict) or not existing.get('sha'):
        return f"{full}/{path} not found", False

    body = {'message': msg, 'sha': existing['sha']}
    if branch:
        body['branch'] = branch
    data, err = _api('DELETE', f'/repos/{full}/contents/{path}', pat, body=body)
    if err:
        return err, False
    return f"Deleted {full}/{path}.", True


def _path_excluded(rel_path: str, patterns: list) -> bool:
    """True if rel_path matches any glob pattern. Checks the full path AND each
    component, so '__pycache__' matches both top-level and nested __pycache__ dirs."""
    parts = rel_path.split('/')
    for pat in patterns:
        if fnmatch.fnmatch(rel_path, pat):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def _file_push_directory(args, username, pat):
    """Bulk-push a local directory as ONE commit via the git data REST API.

    Flow:
      1. Walk local dir, collect (path, bytes) for each file (apply exclude patterns)
      2. POST /git/blobs for each file → blob SHAs
      3. Determine parent commit + base tree (None if repo is empty)
      4. POST /git/trees with all blob entries
      5. POST /git/commits with the new tree (and parent if exists)
      6. PATCH /git/refs/heads/<branch> to point at the new commit (or POST to create the ref)
    """
    repo = args.get('repo', '').strip()
    local_path = args.get('local_path', '').strip()
    msg = args.get('commit_message', '').strip() or "Initial commit"
    branch = args.get('branch', '').strip() or None
    exclude = args.get('exclude') or []

    if not repo or not local_path:
        return "repo and local_path are required for push_directory", False
    full = _resolve_repo(repo, username)

    # Resolve local path safely. Path is relative to the Sapphire root (cwd).
    local = Path(local_path).expanduser().resolve()
    if not local.exists() or not local.is_dir():
        return f"local_path does not exist or isn't a directory: {local}", False

    # SANDBOX: reject paths outside Sapphire's project root, and explicitly
    # reject `user/` (private chats, knowledge DB, plugin signing key) and
    # CONFIG_DIR (encrypted credentials + scramble salt). Without this, an
    # AI prompted to "back up my sapphire install" or a prompt-injected
    # ghost message could push the entire credentials directory to a public
    # GitHub repo — Fernet ciphertext + same-dir salt = recoverable plaintext
    # for every stored PAT, OAuth refresh token, and email password. The
    # plugin signing private key would also be exfiltrated, letting the
    # attacker forge signed malicious plugin updates. 2026-05-14.
    try:
        # .absolute() instead of .resolve() — .resolve() follows symlinks,
        # which breaks SAPPHIRE_ROOT walkup when the plugin is loaded via
        # a junction/symlink (common on Windows dev installs).
        # Memory: symlinked_plugins_resolve_trap.md. herring #24.
        project_root = Path(__file__).absolute().parents[3]
        user_dir = (project_root / 'user').absolute()
        # `local` is fully resolved above but project_root deliberately
        # isn't — when the install path itself is a symlink the two forms
        # diverge, so containment checks both spellings of the root.
        root_real = project_root.resolve()
        if not (local.is_relative_to(project_root) or local.is_relative_to(root_real)):
            return (
                f"local_path must be inside the Sapphire project root, not {local}",
                False,
            )
        if local.is_relative_to(user_dir) or local.is_relative_to(root_real / 'user'):
            return (
                "local_path inside user/ is forbidden — that directory holds "
                "private chats, credentials, and the plugin signing key.",
                False,
            )
        try:
            from core.setup import CONFIG_DIR
            config_dir = CONFIG_DIR.resolve()
            if local.is_relative_to(config_dir):
                return (
                    "local_path inside the config directory is forbidden — it "
                    "holds encrypted credentials and the decryption salt.",
                    False,
                )
        except Exception:
            pass  # if CONFIG_DIR can't be imported here, the user/ guard already covers the typical layout
    except Exception as e:
        return f"Path sandbox check failed: {e}", False

    # Collect files
    files = []
    for fp in local.rglob('*'):
        if not fp.is_file():
            continue
        rel = fp.relative_to(local).as_posix()
        if _path_excluded(rel, exclude):
            continue
        try:
            data = fp.read_bytes()
        except Exception as e:
            return f"Failed to read {rel}: {e}", False
        files.append((rel, data))

    if not files:
        return f"No files to push from {local} (after exclusions)", False

    # Resolve target branch — fall back to repo's default_branch
    if not branch:
        repo_data, err = _api('GET', f'/repos/{full}', pat)
        if err:
            return err, False
        branch = repo_data.get('default_branch') or 'main'

    # Try to find an existing parent commit. Empty repos return 409.
    parent_sha = None
    base_tree_sha = None
    ref_data, ref_err = _api('GET', f'/repos/{full}/git/refs/heads/{branch}', pat)
    if not ref_err and isinstance(ref_data, dict):
        parent_sha = ref_data.get('object', {}).get('sha')
        if parent_sha:
            commit_data, _ = _api('GET', f'/repos/{full}/git/commits/{parent_sha}', pat)
            if isinstance(commit_data, dict):
                base_tree_sha = commit_data.get('tree', {}).get('sha')

    # Create blobs
    tree_entries = []
    for rel, content in files:
        blob_body = {
            'content': base64.b64encode(content).decode('ascii'),
            'encoding': 'base64',
        }
        blob_data, err = _api('POST', f'/repos/{full}/git/blobs', pat, body=blob_body)
        if err:
            return f"Failed to create blob for {rel}: {err}", False
        tree_entries.append({
            'path': rel,
            'mode': '100644',
            'type': 'blob',
            'sha': blob_data['sha'],
        })

    # Build tree (with base_tree if updating an existing branch — preserves files we didn't push)
    tree_body = {'tree': tree_entries}
    if base_tree_sha:
        tree_body['base_tree'] = base_tree_sha
    tree_data, err = _api('POST', f'/repos/{full}/git/trees', pat, body=tree_body)
    if err:
        return f"Failed to create tree: {err}", False

    # Commit
    commit_body = {'message': msg, 'tree': tree_data['sha']}
    if parent_sha:
        commit_body['parents'] = [parent_sha]
    commit_data, err = _api('POST', f'/repos/{full}/git/commits', pat, body=commit_body)
    if err:
        return f"Failed to create commit: {err}", False

    # Update or create ref
    if parent_sha:
        # Branch exists — update it (PATCH) — note GitHub uses POST verb for ref updates? Actually it's PATCH.
        _, err = _api('PATCH', f'/repos/{full}/git/refs/heads/{branch}', pat,
                      body={'sha': commit_data['sha'], 'force': False})
    else:
        # First commit — create the ref
        _, err = _api('POST', f'/repos/{full}/git/refs', pat,
                      body={'ref': f'refs/heads/{branch}', 'sha': commit_data['sha']})
    if err:
        return f"Failed to update ref: {err}", False

    return (
        f"Pushed {len(files)} file(s) to {full}@{branch} as one commit.\n"
        f"  commit: {commit_data.get('html_url') or commit_data['sha'][:7]}"
    ), True


# ─── github_issue actions ─────────────────────────────────────────────────────

def _issue_create(args, username, pat):
    repo = args.get('repo', '').strip()
    title = args.get('title', '').strip()
    body = args.get('body', '')
    if not repo or not title:
        return "repo and title are required for issue create", False
    full = _resolve_repo(repo, username)
    data, err = _api('POST', f'/repos/{full}/issues', pat, body={'title': title, 'body': body})
    if err:
        return err, False
    return f"Filed #{data.get('number')} on {full}: {data.get('html_url')}", True


def _issue_list(args, username, pat):
    repo = args.get('repo', '').strip()
    state = args.get('state', 'open')
    if not repo:
        return "repo is required for issue list", False
    full = _resolve_repo(repo, username)
    data, err = _api('GET', f'/repos/{full}/issues', pat, params={'state': state, 'per_page': 30})
    if err:
        return err, False
    # Filter out PRs (GitHub API mixes them in)
    issues = [i for i in (data or []) if 'pull_request' not in i]
    if not issues:
        return f"No {state} issues on {full}.", True
    lines = [f"  #{i['number']} [{i['state']}] {i['title']} (by {i['user']['login']})" for i in issues]
    return f"Issues on {full} ({state}):\n" + "\n".join(lines), True


def _issue_get(args, username, pat):
    repo = args.get('repo', '').strip()
    number = args.get('number')
    if not repo or number is None:
        return "repo and number are required for issue get", False
    full = _resolve_repo(repo, username)
    data, err = _api('GET', f'/repos/{full}/issues/{number}', pat)
    if err:
        return err, False
    return (
        f"#{data['number']} on {full} [{data['state']}]: {data['title']}\n"
        f"  by {data['user']['login']}\n"
        f"  url: {data['html_url']}\n\n"
        f"{data.get('body') or '(no body)'}"
    ), True


def _issue_comment(args, username, pat):
    repo = args.get('repo', '').strip()
    number = args.get('number')
    body = args.get('body', '')
    if not repo or number is None or not body:
        return "repo, number, and body are required for issue comment", False
    full = _resolve_repo(repo, username)
    data, err = _api('POST', f'/repos/{full}/issues/{number}/comments', pat, body={'body': body})
    if err:
        return err, False
    return f"Commented on #{number}: {data.get('html_url')}", True


def _issue_close(args, username, pat):
    repo = args.get('repo', '').strip()
    number = args.get('number')
    if not repo or number is None:
        return "repo and number are required for issue close", False
    full = _resolve_repo(repo, username)
    data, err = _api('PATCH', f'/repos/{full}/issues/{number}', pat, body={'state': 'closed'})
    if err:
        return err, False
    return f"Closed #{number} on {full}.", True


# ─── github_search ────────────────────────────────────────────────────────────

def _search(args, username, pat):
    stype = args.get('type', '').strip()
    query = args.get('query', '').strip()
    limit = min(int(args.get('limit') or 10), 30)
    if not stype or not query:
        return "type and query are required for search", False

    endpoint = {
        'repos': '/search/repositories',
        'code': '/search/code',
        'issues': '/search/issues',
    }.get(stype)
    if not endpoint:
        return f"Unknown search type '{stype}' (use repos/code/issues)", False

    data, err = _api('GET', endpoint, pat, params={'q': query, 'per_page': limit})
    if err:
        return err, False
    items = (data or {}).get('items', [])
    if not items:
        return f"No results for '{query}'.", True

    lines = []
    for item in items[:limit]:
        if stype == 'repos':
            lines.append(f"  {item['full_name']} ({item.get('stargazers_count', 0)}★) — {item.get('description') or '(no description)'}")
        elif stype == 'code':
            lines.append(f"  {item['repository']['full_name']}/{item['path']}")
        elif stype == 'issues':
            lines.append(f"  {item['repository_url'].split('/repos/')[-1]} #{item['number']} [{item['state']}]: {item['title']}")
    return f"Search results ({stype}, {len(items)} of {(data or {}).get('total_count', '?')}):\n" + "\n".join(lines), True


# ─── Dispatch ─────────────────────────────────────────────────────────────────

_DISPATCH = {
    ('github_repo', 'create'): _repo_create,
    ('github_repo', 'list'): _repo_list,
    ('github_repo', 'get'): _repo_get,
    ('github_repo', 'delete'): _repo_delete,
    ('github_repo', 'fork'): _repo_fork,
    ('github_file', 'read'): _file_read,
    ('github_file', 'write'): _file_write,
    ('github_file', 'delete'): _file_delete,
    ('github_file', 'push_directory'): _file_push_directory,
    ('github_issue', 'create'): _issue_create,
    ('github_issue', 'list'): _issue_list,
    ('github_issue', 'get'): _issue_get,
    ('github_issue', 'comment'): _issue_comment,
    ('github_issue', 'close'): _issue_close,
}


def execute(function_name, arguments, config, plugin_settings=None):
    username, pat, err = _get_github_creds()
    if err:
        return err, False

    if function_name == 'github_search':
        return _search(arguments, username, pat)

    action = (arguments.get('action') or '').strip()
    if not action:
        return f"action is required for {function_name}", False

    handler = _DISPATCH.get((function_name, action))
    if not handler:
        return f"Unknown action '{action}' for {function_name}", False

    try:
        return handler(arguments, username, pat)
    except Exception as e:
        logger.exception(f"github plugin: {function_name}/{action} failed")
        return f"Internal error in {function_name}/{action}: {e}", False
