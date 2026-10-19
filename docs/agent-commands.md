# Choosing the agents that write your cards

`unhoard enrich` runs **two passes** per bookmark. Each pass is a command that gets a prompt
on **stdin** and must print a JSON object on **stdout**. Set them as argv lists (a list, not a shell line,
because bookmark text is untrusted and never goes through a shell).

| Pass | Config key | Sees | May use |
|---|---|---|---|
| 1 read | `[enrich] read_command` | the untrusted post only | the web; no files |
| 2 relate | `[enrich] relate_command` | pass 1's checked summary + your context | local files; **no network** |

The rule: **no agent gets all three of untrusted text, your private data and a way to send data out.**
A hostile post could tell an agent that has all three to put a file into a URL. So pass 1 never sees
your notes, and pass 2 never sees the post or the web. Pass 2 gets only what pass 1 returned, after
this program has checked its fields, and it is told to treat that as untrusted. If pass 1 fails
validation, pass 2 is never started.

Pass 1 answers:

```json
{"summary": "one sentence about the post", "topics": ["short topic", "another"],
 "link_summary": "what the linked page says, or empty", "kind": "tutorial"}
```

Pass 2 answers (this becomes the card):

```json
{"why_saved": "why you probably saved it, and the link to your work if there is one",
 "confidence": 0.7,
 "tasks": [{"title": "Imperative title", "brief": "Scope, what done looks like, what is out of scope",
            "label": "work", "priority": "medium"}]}
```

`tasks` may be empty. At most `[enrich] max_tasks` tasks are accepted; priority is one of
`urgent`, `high`, `medium`, `low`. Anything that fails validation is recorded as an error on that
bookmark and never reaches the queue. Retry with `unhoard enrich --retry-errors`.

## Presets: `[enrich] agent`

The simple way: pick an agent and let the program build the commands each time it runs.

```toml
[enrich]
agent = "claude"     # or "codex"
```

`setup` writes this. Because the argv is built at run time, a flag fix in a later release reaches you
without editing the config. `read_command` / `relate_command` stay as custom overrides for one pass each.
A config that still holds exactly one of the argv lists older releases wrote is treated as the preset,
silently, with no refusal. `unhoard doctor` checks what each pass can really do.

**Claude** (`claude -p`; the JSON inside the `result` envelope is extracted for you):

| Pass | Command (built at run time) |
|---|---|
| read | `claude -p --output-format json --tools WebFetch,WebSearch --allowedTools WebFetch,WebSearch --permission-mode dontAsk --strict-mcp-config --setting-sources "" --disable-slash-commands --no-chrome` |
| relate | `claude -p --output-format json --tools Read,Grep,Glob --permission-mode dontAsk --strict-mcp-config --setting-sources "" --disable-slash-commands --no-chrome --add-dir <folder> ...`, plus `--allowedTools <mcp tools> --mcp-config <generated file>` (and `--disallowedTools <other tools of those servers>`) when memory tools are enabled |

- `--tools` limits the built-in tools. `--strict-mcp-config` makes claude load only the MCP servers named
  by `--mcp-config` (without it **all** your MCP servers, including connectors from your account, load).
  `--permission-mode dontAsk` turns every tool that is not allowed into a denial instead of a prompt.
- **Your own Claude customization is switched off.** `--setting-sources ""` loads no settings file, and
  `--disable-slash-commands` loads no skills. Checked live with claude 2.1.303: without them the read agent,
  which has the web, had your `CLAUDE.md`, the output of your `SessionStart` hooks, your installed plugins and
  your permission allow rules (a server-wide `mcp__server` rule there re-enabled the write tools of a memory
  server the relate pass was limited to). With them, none of that is loaded, and `--mcp-config` servers and
  `--add-dir` folders still work. `--safe-mode` was rejected because it also drops `--mcp-config` servers;
  `--bare` was rejected because it only works with an API key, not with a login. The presets also run claude
  without `CLAUDE_CODE_PLUGIN_DIRS` (plugins named by the environment) and with
  `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`. `--no-chrome` and `CLAUDE_CODE_ENABLE_CFC=0` switch Claude in
  Chrome off (a browser with your logged-in sessions), `ENABLE_CLAUDEAI_MCP_SERVERS=false` keeps the connectors
  of your claude.ai account out, and `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` stops CLAUDE.md files loading (these
  variables are set for any direct custom `claude` command as well). Managed (administrator) settings still apply; nothing can change that.
- The relate pass has no blanket `Read` allow rule: under `dontAsk`, Read/Grep/Glob work in the working
  directory and the `--add-dir` folders only (checked with claude 2.1.303: a read of a path outside them is
  denied). That keeps the relate agent inside your memory, notes and repositories.
- The generated MCP file holds only the enabled servers, copied from your user-level Claude config
  (`~/.claude.json`, top-level `mcpServers`, or `$CLAUDE_CONFIG_DIR/.claude.json`). It is written mode 0600
  in a private temporary directory and deleted after the run, also when the process gets SIGINT, SIGTERM or
  SIGHUP. Its contents are never logged.
- Every agent runs in its own process group; on a timeout, Ctrl-C, SIGTERM or SIGHUP the whole group is killed, so
  MCP servers and shells the agent started do not keep running. A timeout returns promptly even when a helper
  that left the group keeps the output pipe open. (Process groups and SIGHUP are POSIX only; elsewhere only the
  direct child is killed.)
- Both passes run in `<data dir>/scratch`, so a project's own `.claude` settings do not apply. A custom relate
  command runs there too (it used to start in your first context folder).

**Codex** (`codex exec`): `--sandbox read-only` limits only shell commands. By itself it still exposes a
web tool and your MCP servers (checked with codex-cli 0.160.0), so the presets also pass
`--ignore-user-config --ignore-rules -c web_search="disabled" --disable browser_use --disable browser_use_external
--disable computer_use --disable apps --disable image_generation --disable multi_agent`.
`--ignore-rules` skips execpolicy `.rules` files. **Caveat:** `$CODEX_HOME/AGENTS.md` (your global Codex
instructions) is still loaded into the context even with these flags (checked live with codex-cli 0.160.0).
The Codex read pass has no web and no shell, so that text can reach only your model provider.

- **Read pass: no web.** The read pass additionally disables the shell (`shell_tool`, `unified_exec`,
  `view_image`). Enabling `web_search="live"` was tried, but a tool listing still showed `apply_patch`,
  a JavaScript `exec` tool and sub-agent tools next to the web tool, so "no shell, file or MCP tool
  remains" could not be shown. Codex therefore summarises the post text only and cannot open links.
- **Relate pass needs a working Linux sandbox.** Shell commands run inside bubblewrap. Where the kernel
  forbids unprivileged user namespaces, every command fails and Codex reads nothing: it fails closed, so
  nothing leaks, but the cards lose their personal context. `doctor` runs a harmless `cat` of a temp file
  and reports this.
- **No MCP with Codex in this release.** `--ignore-user-config` drops your MCP servers; `status` says so.

## Custom commands

Any other tool works if it reads a prompt on stdin and prints text containing one JSON object, for
example a small script of your own around a local model. Then the network rule is yours to keep:

```toml
[enrich]
read_command   = ["/usr/local/bin/ask-local-model", "--json"]
relate_command = ["/usr/local/bin/ask-local-model", "--json"]
```

Older configs with a single `[enrich] command` still work: it runs for both passes and `status` shows a
warning. Set `agent`, or split it, to get the protection above.

**Guard (custom commands only; presets never trigger it).** The guard reads a command that starts with
`claude` (any path) structurally, the way claude reads its command line: `--tools` is variadic, so every
value up to the next flag counts (`--tools Read Bash` has two tools); a value option such as
`--append-system-prompt` takes the next token as its value even when that token starts with `-` (so
`--append-system-prompt --setting-sources ""` sets **no** setting sources); `--` ends the options. It refuses to
start (nothing runs) a custom Claude command when:

- it uses a **flag that is not on the allow-list**. Allowed: `-p/--print`, `--output-format`, `--input-format`,
  `--tools`, `--allowedTools`, `--disallowedTools`, `--permission-mode`, `--permission-prompts`,
  `--strict-mcp-config`, `--setting-sources`, `--disable-slash-commands`, `--safe-mode`, `--bare`, `--no-chrome`,
  `--model`, `--fallback-model`, `--effort`, `--append-system-prompt[-file]`, `--system-prompt[-file]`,
  `--system-prompt-snapshot`, `--max-budget-usd`, `--json-schema`, `--verbose`, `--no-session-persistence`,
  `--include-hook-events`, `--include-partial-messages`, `--exclude-dynamic-system-prompt-sections`,
  `--restricted`; and for relate only `--add-dir`, `--mcp-config`, `--system-prompt-file` and
  `--append-system-prompt-file` (they read files). Any other flag could enable something
  this program cannot see, so it is refused. An unknown flag is read as taking the next token as its value.
- it uses `--dangerously-skip-permissions`, `--allow-dangerously-skip-permissions`, `--settings`,
  `--managed-settings`, `--plugin-dir`, `--plugin-dir-no-mcp`, `--plugin-url`, `--chrome`, `--ide`,
  `--inherit-permission-mode`, `--agent`, `--agents`, `--channels`, `--sdk-url` or
  `--permission-mode bypassPermissions`.
- it has an **operand**: a token that is neither an option nor an option value, also after `--`. claude reads a
  first operand as a subcommand (`mcp serve`, `plugin install x`, `rc`) or as the prompt, and the prompt arrives
  on stdin.
- a tool list (`--tools`, `--allowedTools`, `--disallowedTools`) has nested or unbalanced parentheses, or a
  `--tools` entry has a `(` at all. Lists are split exactly like claude 2.1.303 splits them (commas and spaces
  outside one parenthesis; the first `)` ends it), and the program refuses the only inputs where it and claude
  could read a list differently.
- the pass has no `--tools` list, or the list is `default` (all tools), or it names a tool outside the
  allow-list of the pass: `WebFetch`, `WebSearch` for read; `Read`, `Grep`, `Glob` for relate. Unknown tools such
  as `Bash`, `PowerShell`, `Agent`, `Edit` or `Write` are refused, in `--tools` and in `--allowedTools`.
  **The read pass refuses every `mcp__` entry** and `--add-dir`.
- it lacks `--strict-mcp-config`; for relate it allows `--mcp-config` only with `--permission-mode dontAsk` and
  an `--allowedTools` list of single named tools (`mcp__server` and wildcards are refused, with or without
  `--mcp-config` in the same command).
- a **read** command does not isolate your Claude customization (`--setting-sources ""`, or `--safe-mode`):
  otherwise your `CLAUDE.md` and hooks would sit next to the web. A **relate** command without it only gets a
  warning, because its tools are already limited.

A custom relate command whose `--allowedTools` has a bare `Read`, `Grep` or `Glob`, or an absolute or home path rule
(`Read(//**)`, `Read(~/x)`) is not refused but gets a warning: that rule lets the agent read any file you can read, not
only the context folders.

`--disallowedTools WebFetch` and other text that merely mentions a tool are not refused. A custom command whose
program is not `claude` (`env claude`, `npx @anthropic-ai/claude-code`, a script, `codex`) cannot be read: `status`,
`doctor` and `enrich` print **"cannot verify isolation"** for it, and the rule is yours to keep.
The guard also reads the command a second time after the context folders were appended with `--add-dir`.
`[enrich] allow_network_in_relate = true` (relate) and `allow_private_in_read = true` (read) are the explicit
per-pass overrides: they let a command with unknown flags or any other refusal above start, with a warning.
They are unsafe: do not set them.

## doctor

```bash
unhoard doctor
```

Starts each pass with a trivial prompt (a few tiny agent calls) and prints a pass/fail table.

- **Claude, deterministic.** It runs with `--output-format stream-json --verbose --include-hook-events` and reads the
  `system`/`init` event, which lists the tools, MCP servers and plugins the process loaded, and counts the
  `hook_started` events. Read pass: tools are a subset of WebFetch/WebSearch and no MCP server. Relate pass: no
  `Web*` or `Bash` tool, only Read/Grep/Glob, MCP tools only from enabled servers, `dontAsk` active.
- **Effective tools.** For the relate pass it computes the MCP tools the agent can really call (exposed by a
  loaded server and named by `--allowedTools`, minus `--disallowedTools`), not just the ones you selected, and
  fails when any is not a selected read-only tool.
- **No browser or connectors.** Both passes must show no Claude in Chrome tool or server and no claude.ai
  connector in the init event.
- **Private context.** Both passes must show no hook that ran, no plugin of yours and no skill, and the agent is
  asked to quote the first line of any user-level instruction file (`CLAUDE.md`, `AGENTS.md`) or hook text in its
  context. The doctor compares the quote with your own instruction files locally; a quote that matches is a
  FAIL (it also compares against `~/CLAUDE.md` and `~/AGENTS.md`). Your files' text is never put into the prompt.
- The doctor remembers the tool names a server exposed (names only, `<data dir>/mcp-tools.json`). The next
  relate run passes every tool of an enabled server that is not selected as `--disallowedTools`.
- **Codex, advisory.** There is no init event, so the model is asked to list its tools; a surprise is a
  warning, not a proof. It also reports whether the sandbox can run a command.
- Custom commands are skipped, with a "cannot verify isolation" warning when they are not a direct `claude`.

## Relevance: what the relate pass can see

`unhoard setup` (or `unhoard context discover` at any time) looks for what you do,
shows the result grouped by kind, asks once `Use these? [Y/n]` (`--yes` or no terminal: accepts) and writes
it **explicitly** into `[context]`, where you can read and edit it. `status` shows what is in use. Nothing is
read from these places unless it is listed there.

```toml
[context]
memory_folders = ["~/.claude/projects/-home-me/memory"]   # agent memory (Claude projects, ~/.codex/memories)
notes_folders  = ["~/Notes/vault"]                          # Obsidian vaults, Logseq graphs, ~/notes
folders        = []                                         # anything else you want it to read
repos          = ["~/code/my-project"]                      # git repos active in the last 30 days (top 10)
index_files    = ["~/.claude/CLAUDE.md", "~/.claude/projects/-home-me/memory/MEMORY.md"]
mcp_servers    = ["gbrain"]                                 # user-level Claude MCP servers (known memory servers)
mcp_tools      = []                                         # extra tools by hand, see below
commands = [["my-search", "{topics}"]]                      # optional read-only helpers (no shell, no network)
max_chars = 12000
```

- `setup` and `context discover` ask once on a terminal. With no terminal and no `--yes` (an agent running
  them, cron) nobody confirmed, so only recent Git repositories are saved. Agent memory folders, notes vaults,
  your global `CLAUDE.md` / `AGENTS.md`, `MEMORY.md` index files and memory MCP servers are **not** saved, and
  the command prints which lists it withheld. Run `unhoard context discover --yes` to save everything.
  The config file is replaced atomically (a symlinked config stays a symlink) and every other key is checked
  to be unchanged.
- The folder search stays on your home folder's file system and skips network mounts (NFS, SMB, sshfs...).
- Discovery looks in `~/.claude/projects/*/memory`, `~/.codex/memories`, folders with `.obsidian` or
  `logseq/config.edn` (searched under your home to depth 4, time-boxed to about 3 s, skipping
  `node_modules`, caches, hidden folders and trash), `~/notes`, `~/Notes`, `~/Documents/notes`, and Git
  repositories with a commit in the last 30 days (preferring commits by your `git config user.email`).
- The relate agent may read the listed folders (and only those); the prompt names each folder with its kind
  (agent memory, notes vault, Git repository) and tells it to read the memory indexes first, grep the
  folders for the post's topics, call the memory tools and name the concrete link in `why_saved`. Zero tasks
  stays a valid answer. It waits up to `[enrich] relate_timeout` seconds (default: at least 600, because it
  searches and calls tools).
- `index_files` are inlined into the prompt (each capped, the whole pack capped by `max_chars`). When no
  `about_me_file` is set they are the default "about the reader".
- The text of your files and tool results reaches the relate agent only. The raw post never does.
- `commands` run without a shell, in their own process group (killed on `command_timeout`), with the X token
  removed from their environment. Their output goes into the relate prompt, so such a command must only read local data:
  it must not reach the network and must not be an AI agent.

**Memory MCP servers.** For the Claude preset, `mcp_servers` names user-level servers from
`~/.claude.json`. Only servers in a built-in registry are enabled, and only with the registry's read-only
tools. Registry rule: a tool qualifies only if it does not write, does not take a URL or fetch, and does
not send messages.

| Server (matched by command/url pattern; a name-only match warns) | Tools allowed |
|---|---|
| `gbrain` | `query`, `search`, `get_page`, `get_links`, `get_backlinks`, `get_timeline`, `list_pages`, `resolve_slugs` |
| `basic-memory` | `search_notes`, `read_note`, `build_context`, `recent_activity` |
| `memory` (modelcontextprotocol server-memory) | `read_graph`, `search_nodes`, `open_nodes` |

Unknown servers are **not** enabled; `status` lists them as "not used". To use one, add its tools by hand:

```toml
[context]
mcp_tools = ["mcp__mynotes__search", "mcp__mynotes__read"]
```

That is your responsibility: list only tools that read, take no URL and send nothing out. Each entry must
name one tool: `mcp__server` and `mcp__server__*` are ignored (with a warning), because they would allow the
server's write tools too. Entries are split on commas and spaces first (so `"a, b"` is two tools), and one that
still holds a parenthesis, wildcard or whitespace is ignored too. A server is matched to the registry by its command or URL; a match on the name alone
(any server can be called `memory`) is used but `status` and `doctor` warn about it. A cloud-hosted
memory server receives the query text (derived from the bookmark), but that is your own provider, never a
destination an attacker chose. MCP is not used with Codex in this release.

## Safety notes

- The X token is removed from every agent's environment, and from the environment of the program the `command`
  launch adapter starts after Go (together with any `UNHOARD_*` variable that looks like a secret).
- Both passes run in `<data dir>/scratch` (presets), not in a project of yours.
- Bookmark text is fenced as untrusted data in the pass 1 prompt, and each answer is validated field by
  field. A task still only starts when you press Go.
- After Go, the worker's brief tells it to research the post's claims and your current work first (web and
  local), with the post and its summaries fenced as untrusted data.
