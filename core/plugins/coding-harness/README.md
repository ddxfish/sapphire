# Coding Harness

File and shell tools for coding work: read, write, edit, list and search files,
and run commands, all opening in one working directory. On by default (the
setup wizard offers it). Hidden in managed installs.

## Tools

| Tool | What it does |
|---|---|
| `read_file(path, start_line?, end_line?)` | A text file with line numbers, 500 lines at a time |
| `write_file(path, content, append?)` | Create or overwrite; parent folders are made |
| `edit_file(path, old_text, new_text, replace_all?)` | Replace exact text; it must appear once unless `replace_all` |
| `list_files(path?, pattern?)` | A directory listing; `pattern` is a glob, `**/*.py` recursive |
| `search_files(pattern, path?, glob?, max_results?)` | Regex search, `file:line` matches (50 by default) |
| `run_command(command, cwd?, timeout?, max_output?)` | A shell command in a fresh shell; 120 s timeout by default; a timeout kills the whole process tree |

Paths are relative to the working directory.

## Settings

Settings > Plugins > **Coding Harness**:

| Setting | Default | |
|---|---|---|
| `working_dir` | `user/sapphire-workspace` | Where every tool opens. Relative = under the Sapphire root. Made on first use |
| `sandbox` | on | Keeps the file tools, and each command's starting directory, inside the working directory |
| `output_limit` | 6000 | Characters returned by a read or a command |
| `max_timeout` | 300 | Ceiling in seconds for any command |
| `blacklist` | `rm -rf /`, `mkfs`, `dd if=/dev`, ... | Substrings `run_command` refuses, one per line |

## Good to know

The sandbox confines file paths and the starting directory only. A command
runs in a real shell and can still reach outside (`cat ../file`); the
blacklist is the guardrail for commands, and it is a substring match, not a
jail. Only put this plugin in a toolset you would hand a shell to.
