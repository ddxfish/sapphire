# GitHub Plugin

Real GitHub tools for Sapphire. PAT-authenticated, multi-account, scope-aware.

Lets your AI manage repos, edit files (single or bulk), file issues, and search — all using the active scope's GitHub identity. Switch scopes to switch accounts.

## What it does

- **`github_repo`** — create / list / get / delete / fork
- **`github_file`** — read / write / delete a single file, or **`push_directory`** to bulk-push a local directory as one clean commit
- **`github_issue`** — create / list / get / comment / close (issues are authored by the scope's account)
- **`github_search`** — repos / code / issues

All tools call `api.github.com` directly using `requests` — no `git` binary, no PyGithub, no new dependencies.

## Setup

### 1. Generate a fine-grained PAT

GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token.

Required permissions:

| Scope | Access |
|-------|--------|
| Contents | Read and write |
| Metadata | Read |
| Pull requests | Read and write |
| Issues | Read and write |
| Administration | Read and write *(only if you want repo create/delete)* |

Repository access: **All repositories** (broad) or **Selected repositories** (recommended — opt in to specific repos).

> Classic PATs work too if you must — `repo` scope covers the basics. Fine-grained is strongly preferred because it scopes the AI to specific repos at the token level.

### 2. Add an account in Sapphire

Settings → Plugins → GitHub. For each account:

1. Paste the PAT
2. Click **Validate** — confirms the token works and auto-fills the username
3. Optional: name the scope (e.g. `default`, `sapphireprime`, `work`) and a display label
4. Save

Multiple accounts? Just add them under different scopes.

### 3. Pick a scope

In any chat, the GitHub scope dropdown in the sidebar selects which account is "live." All `github_*` tool calls in that chat run as that account.

## Acceptable Use

This plugin uses **your** GitHub account via **your** PAT. You are responsible for what your AI does with it.

GitHub's [Acceptable Use Policy](https://docs.github.com/en/site-policy/acceptable-use-policies) prohibits:

- Mass-creating issues, PRs, or comments across repos you don't own
- Spam or unsolicited promotional content in issues, PRs, or comments
- "Information harvesting" — using API access to collect user data for outreach
- Circumventing rate limits

If your AI does any of the above, GitHub will **revoke your token** and may suspend the account. This plugin doesn't change that risk surface — `gh` CLI, `octokit`, and any Python script using `requests` have the exact same capabilities. The plugin is a tool. Use it on **repos you own or are a collaborator on.**

To enforce that at the token level, use a fine-grained PAT scoped to specific repos.

## Rate limits

Authenticated PAT calls are limited to 5,000 requests per hour by GitHub. Sapphire makes one or two API calls per tool invocation (push_directory makes more — roughly one per file plus a few overhead calls). You'd have to push thousands of files in an hour to feel the limit.

## Tool details

### `github_repo`

```
github_repo(action="create", name="my-plugin", private=False, description="...")
github_repo(action="list")
github_repo(action="get", repo="ddxfish/sapphire")     # full owner/name
github_repo(action="get", repo="sapphire")             # just name = your own
github_repo(action="delete", repo="my-plugin")          # be careful
github_repo(action="fork", repo="ddxfish/sapphire")
```

### `github_file`

```
github_file(action="read", repo="owner/name", path="README.md", ref="main")
github_file(action="write", repo="owner/name", path="docs/x.md",
            content="...", commit_message="Update x", branch="main")
github_file(action="delete", repo="owner/name", path="old.txt",
            commit_message="Remove old", branch="main")

# Bulk push — one commit, one tool call:
github_file(action="push_directory",
            repo="sapphireprime/bitcoin",
            local_path="core/plugins/bitcoin",
            commit_message="Initial commit",
            exclude=["__pycache__", "*.pyc"])
```

`push_directory` reads the local directory recursively, base64-encodes each file, builds a git tree via the GitHub data API, and creates a single commit. Works on empty repos (creates the initial branch) and existing branches (incrementally adds/updates files).

### `github_issue`

```
github_issue(action="create", repo="ddxfish/sapphire",
             title="Add X", body="It would be cool if...")
github_issue(action="list", repo="ddxfish/sapphire", state="open")
github_issue(action="get", repo="ddxfish/sapphire", number=42)
github_issue(action="comment", repo="ddxfish/sapphire", number=42, body="...")
github_issue(action="close", repo="ddxfish/sapphire", number=42)
```

### `github_search`

```
github_search(type="repos", query="sapphire ai", limit=10)
github_search(type="code", query="def execute_function repo:ddxfish/sapphire")
github_search(type="issues", query="is:open author:sapphireprime")
```

## Implementation notes

- **No new dependencies.** Pure `requests` (already a Sapphire dep) + stdlib (`base64`, `pathlib`, `fnmatch`).
- **PAT storage.** Tokens are stored in `core/credentials_manager` under `github_accounts[scope]`, scrambled at rest using the same machine-identity key as every other credential.
- **Scope mechanics.** The plugin declares a `github` scope in its manifest. The scope ContextVar is registered automatically by `plugin_loader` and toggled via the sidebar dropdown.
- **Bulk push details.** `push_directory` does the standard git-data-API dance: blob-per-file, tree, commit, ref update. ~one POST per file plus a handful of overhead calls. For a small plugin (< 30 files) this is well under any rate limit and gives you one clean commit instead of N noisy ones.

## What V1 doesn't include

- Pull requests (V1.5 — needs branch ops in `github_file` first)
- Repo transfer (do it manually in the GitHub UI; safer)
- Webhooks, releases, gists, projects, actions

If you want any of these, file an issue.
