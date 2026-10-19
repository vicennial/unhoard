# unhoard

**Made for [Omnigent](https://github.com/omnigent-ai/omnigent). Works with any agent.**

Your X (Twitter) bookmarks aren't a graveyard anymore. Your AI agent reads every bookmark, works out why you saved it,
and writes a task you can start with one click.

unhoard syncs your X bookmarks into a local SQLite file, has your own agent (Claude Code, Codex, or any command that
reads stdin and prints JSON) write "why you probably saved this" plus up to a few concrete tasks per bookmark, and shows
them as cards on a page that only listens on your own machine. You press **Go**, **Later** or **Skip**, a few cards a
day. Nothing starts until you press Go.

[Quick start](#quick-start) · [How it works](#how-it-works) · [Why Omnigent first](#why-omnigent-first) ·
[X API access](#x-api-access) · [Commands](#commands) · [Privacy](#privacy-and-safety) ·
[Compared with other bookmark tools](#how-unhoard-compares-with-other-x-bookmark-tools) · [FAQ](#faq) ·
[Phone](https://github.com/vicennial/unhoard/blob/main/docs/phone.md) · [Card API](https://github.com/vicennial/unhoard/blob/main/docs/card-api.md) · [Agent commands](https://github.com/vicennial/unhoard/blob/main/docs/agent-commands.md)

![unhoard: the card page turns saved posts into tasks, and Go starts one](https://raw.githubusercontent.com/vicennial/unhoard/main/docs/images/demo.gif)

> Status: **v0.1, experimental.** Apache-2.0 licensed. Python 3.11 or newer, no dependencies.

## What to do with your X bookmarks

Saving a post takes one tap; acting on it takes a decision you never get around to. Bookmarks pile up because the
"why" is lost the day after. unhoard keeps the part that is cheap for a person (deciding) and hands the expensive part
to your agent: reading the post, remembering why it mattered to *you*, and turning that into a task with a brief the
agent can start on.

- **It writes the why, not a summary.** A second pass looks at your notes, memory folders and recent repositories
  (only the ones you listed) to connect the post to your actual work.
- **It is a short queue, not another inbox.** Three cards a day by default; the rest wait.
- **Safe by construction.** The agent that reads the untrusted post never sees your files, and the agent that sees
  your files never sees the post or the web. See [Privacy and safety](#privacy-and-safety).
- **Local first.** SQLite on your disk, a server on `127.0.0.1`, your X login in a 0600 file.

## How it works

1. **Sync.** `unhoard sync` pulls your bookmarks from the X API into a local SQLite file (incremental, idempotent).
2. **Your agent writes the why and the task.** `unhoard enrich` runs your agent in two passes per bookmark: one reads the
   post, one connects it to your context. Out come "why you probably saved this" and proposed tasks, ranked
   (priority, then confidence, then newest).
3. **Press Go.** `unhoard serve` opens the card page. Go starts the task: as a **session in the "X Bookmarks" project in
   Omnigent**, or by **running your own command** with the brief on stdin, or, with neither set up, by **showing the
   brief** so you can paste it anywhere. Later hides the card for a few days; Skip drops it.

![The card page with fictional demo posts: today's three picks, each with a Show brief (Go), Later and Skip button](https://raw.githubusercontent.com/vicennial/unhoard/main/docs/images/cards.png)

*The page above is `unhoard try` with the fictional sample data; there the first button says "Show brief" because nothing
is set up to start a task (with Omnigent or your command it says "Go"). The demo people, posts, numbers and links are made up:
their handles contain a dot, which X handles cannot, so none of them is a real account, and the portraits are
AI-generated (Nano Banana Pro) and show nobody real.*

## Quick start

Python 3.11 or newer. There are no dependencies.

**Look first, with no account and nothing saved on your machine** (uses a temporary folder with fictional sample
cards, and removes it when you stop):

```bash
uvx unhoard try
# or, straight from the repository:
uvx --from git+https://github.com/vicennial/unhoard unhoard try
```

**Install:**

```bash
uv tool install unhoard            # or: pipx install unhoard
# or straight from the repository:
uv tool install git+https://github.com/vicennial/unhoard    # or: pipx install git+https://github.com/vicennial/unhoard
```

**Use your own bookmarks:**

```bash
unhoard setup               # writes the config, asks a few questions, finds Omnigent, your notes and repos
unhoard login x             # opens X in your browser; needs your own X developer app (see X API access)
unhoard sync                # pull your bookmarks
unhoard enrich --limit 5    # have your agent write the why and the tasks (a few first, to check the cost)
unhoard serve               # http://127.0.0.1:8765
```

`unhoard doctor` checks what each enrichment agent can really do (a few tiny agent calls) and what Go will do.
`unhoard demo` adds the fictional sample cards to your own database if you want to look around before you have synced.
Run `sync` and `enrich` on a schedule with [cron, a Claude Code `/loop`, or Hermes cron](https://github.com/vicennial/unhoard/tree/main/examples/schedule/).

Open <http://127.0.0.1:8765>. Today's picks are a row of stations, each with the post as it looks on X, why you
probably saved it, the task with its brief and any links in it, a note field and the **Go / Later / Skip** buttons.
The rest wait in a list below; open any row to answer it in place. It works at phone width (through your own port
forwarding if you want it; the server only ever listens on loopback). `next` and `decide` do the same in a terminal.

### What Go does

Set `[launch] adapter` in `config.toml`. The default is `auto`:

- `auto` (default): an Omnigent session when `[omnigent] agent_id` is set **and** the Omnigent API answers on this machine
  when you press the button; otherwise it shows the brief. `unhoard setup` and `unhoard agents` find your local Omnigent
  and save the agent you pick. An explicit adapter below always wins.
- `omnigent`: always create a session on your Omnigent server with the task brief as the first message
  (`POST {api}/v1/sessions`). Set `[omnigent] agent_id` to a registered agent (`unhoard agents` lists them;
  `host_id`, `workspace`, `harness`, `model` and `reasoning_effort` are optional). Sessions go into the Omnigent
  project (sidebar folder) named by `project`, "X Bookmarks" by default, created on the first Go; `project = ""` for none.
  The session id is shown on the card page under "Launched".
- `command`: pipe the brief to a program you name (an argv list, detached). This is how any other agent runs your tasks.
- `print`: show the brief on the page (plain text, with a Copy button) and in the terminal running the server, so
  you can paste it anywhere.

The button says **Go** when it starts something (an Omnigent session or your command) and **Show brief** when it only
shows the brief. If Omnigent starts or stops after the page loaded, the server refuses the click instead of doing
something else (the page reloads and the button changes; nothing starts and the card stays). With `auto`, an agent
chosen and Omnigent not running, `unhoard decide ... go` fails and the card goes back to the queue. A failed launch puts the card back in the queue and shows the reason. `status` and `doctor` show the
configured adapter and the one that applies now.

### From your phone

You can start and answer cards from your phone over your own private network. It is opt-in: the server still listens
on `127.0.0.1` only, and nothing changes unless you set it up. The recommended route is Tailscale:

```bash
unhoard phone                     # prints the steps for this machine; changes nothing
unhoard phone --yes               # also writes [server] allowed_hosts to your config
tailscale serve --bg 8765         # you run this, to share the page with your tailnet (never `funnel`)
```

![The brief panel on a phone after pressing Show brief](https://raw.githubusercontent.com/vicennial/unhoard/main/docs/images/phone-brief.png)

Anyone on your tailnet can press Go, so share it only with people you trust. An SSH tunnel works with no config
change. Details and the security notes: [docs/phone.md](https://github.com/vicennial/unhoard/blob/main/docs/phone.md).

## Why Omnigent first

[Omnigent](https://github.com/omnigent-ai/omnigent) gives each task a visible, resumable session in one project
folder ("X Bookmarks"), in the same web UI you already use for your other Omnigent sessions. You press Go, the task
shows up as a session you can open, watch, steer and come back to later, instead of vanishing into a terminal.

Other agents work through the `command` adapter: unhoard starts your program with the brief on stdin, so any agent CLI
or script that takes a prompt on stdin fits. They get the same brief; they just don't get the session list.

### Coming: an Omnigent sidebar tab

A **Bookmarks** entry in the Omnigent sidebar is planned. It needs Omnigent extension API support that does not exist
yet: starting sessions from an extension page, and reading a local data source. Until then, use the card page.
`src/unhoard_ext/` holds a scaffold that only explains this.

## How unhoard compares with other X bookmark tools

Other good open-source tools mostly *organize* your bookmarks: they archive, tag or search them. unhoard is for
*acting* on them: a few tasks a day, and your agent does the work when you press Go. As of October 2026, from each
project's own README:

| Tool | What it does with your bookmarks | How it reads X | Runs on |
| --- | --- | --- | --- |
| **unhoard** | Your agent writes why you saved each one and a task; you answer a few cards a day; Go starts the task in Omnigent or any agent CLI | Official X API (OAuth 2.0, your own app) | Python, no dependencies |
| [Siftly](https://github.com/viperrcrypto/Siftly) | Searchable, categorized knowledge base with a mindmap view | Bookmarklet or console-script import | Node web app |
| [Smaug](https://github.com/alexknowshtml/smaug) | Archives bookmarks to markdown, categorized by Claude Code or OpenCode | bird CLI with your browser session cookies | Node |
| [Field Theory CLI](https://github.com/afar1/fieldtheory-cli) | Local bookmark library your agent can search through a skill | Browser session sync or OAuth API | Node, with a Mac app |
| [birdclaw](https://github.com/steipete/birdclaw) | Your whole X history (archive import) in a local web app, CLI and read-only MCP server | X archive file plus cached live reads | Node |
| [x-bookmarks](https://github.com/sharbelxyz/x-bookmarks) | OpenClaw skill: your agent fetches bookmarks and proposes actions in chat | bird CLI or X API v2 | OpenClaw skill |

Choose unhoard if you want bookmarks to turn into finished work, with nothing starting until you press Go. Choose an
archive tool if you mainly want to search what you saved. They combine fine: unhoard only reads.

## X API access

You need your own X API access and your own X developer app (OAuth 2.0 enabled; "Native App" is fine). Easiest: set the
app's callback URL to `http://127.0.0.1:8766/callback`, then run `X_CLIENT_ID=... unhoard login x` and approve it in the
browser. The login is kept in a 0600 file in the data dir and refreshed automatically; `unhoard logout x` removes it.
Setup and security notes: [docs/x-login.md](https://github.com/vicennial/unhoard/blob/main/docs/x-login.md). Instead, you can still export `X_BEARER_TOKEN` or
set `[x] bearer_token`; those win over a saved login. `status` and `doctor` show which one is used, never the token.

Reading bookmarks (`GET /2/users/{id}/bookmarks`) needs an **OAuth 2.0 user-context token with the `bookmark.read`
scope**, which typically needs a paid X API plan (check X's current pricing). An app-only bearer token does not work.
X does not return *when* you bookmarked a post, so the page shows when the post was published. If you cannot use the
API, `sync --from-file` accepts a saved response in the same JSON shape.

## Paste this into Claude Code or Codex to install and configure it

Or install the agent skill, which teaches Claude Code, Codex and other agents to set up and run unhoard for you:
`npx skills add vicennial/unhoard` (the skill is in [skills/unhoard](https://github.com/vicennial/unhoard/tree/main/skills/unhoard)).

```text
Install and configure unhoard from this repository for me.
1. Check Python >= 3.11, then run `pip install .` in a virtual environment and show me the path of
   the `unhoard` command.
2. Run `unhoard setup`. For "agent that writes the cards" use the CLI I am running right now.
3. Run `unhoard demo` then `unhoard serve` and tell me the URL so I can look.
4. Ask me whether I have X API access with a user token that has the bookmark.read scope. Do not ask me to
   paste the token into this chat: tell me to `export X_BEARER_TOKEN=...` in my own shell or put it in a
   600-mode env file, and wait.
5. When I confirm, run `unhoard sync`, then `unhoard enrich --limit 5`, then
   `unhoard status`, and summarise the result.
6. Run `unhoard doctor` and show me the table. Review the `[context]` lists that setup wrote with
   me. Setup run without a terminal does not save agent memory folders or memory MCP servers: show me what
   `unhoard context discover --dry-run` found and let me decide whether to run it with `--yes`.
   Optionally ask for a short text file about my work and set [enrich] about_me_file to it.
7. Offer to add a schedule from examples/schedule/. Never answer cards for me and never run
   `unhoard decide`: only I decide.
```

## Commands

| Command | What it does |
| --- | --- |
| `try [--port N] [--no-open]` | The card page with fictional sample cards in a temporary folder that is removed on exit. Touches none of your own config or data; Go only shows the brief. |
| `setup [--yes] [--force] [--no-discover]` | Writes `config.toml` (mode 0600; asks about your X user id, agent and launch adapter on a terminal), looks for a local Omnigent and your agents, then runs `context discover`. |
| `login x [--client-id ID] [--port N] [--timeout SECONDS] [--no-browser] [--replace]` | Logs in to X in the browser (OAuth 2.0 + PKCE). `--timeout` is how long to wait for the browser (default 300 s). If a login is saved, it asks `[y/N]` before signing it out; with no terminal it refuses unless you pass `--replace`. `logout x` revokes and deletes the saved login. |
| `context discover [--yes] [--dry-run]` | Finds memory folders, notes, repos and memory servers and saves them to `[context]`. Without a terminal and without `--yes` it does not save agent memory folders or memory MCP servers. |
| `phone [--host NAME] [--port N] [--yes]` | Prints the steps to use the card page from your phone over Tailscale (or SSH). Reads this machine's Tailscale name; changes nothing unless `--yes` writes `[server] allowed_hosts`. |
| `agents [--yes]` | Lists the agents on your local Omnigent server and saves the one Go should start. |
| `doctor` | Shows what Go will do, then starts each enrichment pass with a trivial prompt and checks the tools it really has. |
| `sync [--full] [--refresh] [--max-items N] [--from-file JSON]` | Pulls new bookmarks. `--refresh` re-reads the pages and updates avatars, counts, media and links of bookmarks you already have (no decision or enrichment changes). `--from-file` ingests a saved X API response. |
| `enrich [--limit N] [--retry-errors]` | Has your agent write why-saved notes and tasks for unprocessed bookmarks. |
| `next [--more] [--json]` | Shows the best open card. `--more` ignores the daily limit. |
| `decide ID go\|later\|skip [--note ...]` | Answers a card. Go runs the launch adapter; a failed launch puts the card back. |
| `serve [--port N] [--no-open]` | The card page on 127.0.0.1. |
| `status` | Paths, counts, token present (yes/no) and its source, the agent, the configured and effective launch adapter, context and MCP tools in use, config problems. |
| `demo` | Adds fictional sample cards to your database. |

## Configuration

`unhoard setup` writes a commented `config.toml`. Main settings:

- `[enrich] agent = "claude" | "codex"`: the program builds both agent commands when it runs, so fixes reach
  you. `read_command` / `relate_command` (argv lists, prompt on stdin, JSON on stdout) remain as custom
  overrides. Details and the Codex caveats are in [docs/agent-commands.md](https://github.com/vicennial/unhoard/blob/main/docs/agent-commands.md).
- `[context]`: what pass 2 may use to connect a post to your work. `setup` and
  `unhoard context discover` fill it in for you (see below); `status` shows what is in use.
  `[enrich] about_me_file` is optional.
- `[queue] daily_cards = 3`, `later_days = 3`.
- `[server] allowed_hosts = []`: extra host names the card page answers to (default none: loopback only). For phone access, see [From your phone](#from-your-phone). Host names only: no scheme, port, wildcard or IP address. Requests by such a name must come through an HTTPS proxy (`tailscale serve`), never with the server's own port, and carry Tailscale's identity header.
- `[server] require_tailscale_identity = true`: set `false` only behind another HTTPS proxy you trust; it turns off the identity-header check (see [docs/phone.md](docs/phone.md)).
- `[launch] adapter`: `auto` (default: Omnigent when it runs and an agent is chosen, else show the brief), `print` (show the brief), `command` (pipe it to a program you name, detached),
  or `omnigent` (create a session: `POST {api}/v1/sessions` with your `agent_id`, in the `project` folder,
  default "X Bookmarks"; optional `host_id`, `workspace`, `harness`, `model`).

Where things live: data in `$UNHOARD_HOME` (else the XDG data dir), config in
`$UNHOARD_CONFIG` (else `~/.config/unhoard/config.toml`). Paths are resolved to
absolute form once. **Agents and cron jobs may run with a different HOME than your shell, so export both
variables for them.** The schedule examples do this.

Run it on a schedule with [cron, a Claude Code `/loop`, or Hermes cron](https://github.com/vicennial/unhoard/tree/main/examples/schedule/).

## What it reads by default

Only what `setup` / `context discover` lists in `[context]` after you accepted it (and you can edit it):

- agent memory folders (`~/.claude/projects/*/memory`, `~/.codex/memories`), note vaults (Obsidian, Logseq,
  `~/notes`) and Git repositories you committed to in the last 30 days;
- `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md` and each memory folder's `MEMORY.md`, inlined (capped);
- read-only search tools of memory MCP servers you already have, if they are known ones (`gbrain`,
  `basic-memory`, the `memory` knowledge-graph server). Other servers are never used unless you list their
  tools yourself. A cloud-hosted memory server receives the search text, which is your own provider.

Only the relate pass sees these, and it has no network. The raw post never reaches it (only pass 1's checked summary does), and the read pass
never sees any of it. Neither Claude pass loads your own Claude customization (CLAUDE.md, hooks, settings,
plugins, skills); `doctor` checks that. Codex supports folders but no MCP, and needs a working Linux sandbox for
its relate pass (`doctor` checks it).

The Claude presets use `--no-chrome`; they are tested with Claude Code 2.1.303 (an older version may lack the flag).

## Privacy and safety

- Everything is local: a SQLite file (mode 600) on your machine and a server on `127.0.0.1`.
- The **only** place bookmark text leaves your machine is the agent command *you* configure, which may
  send it to a model provider. The X token is removed from that command's environment, and the command
  runs in a scratch directory, not in your projects.
- **No agent gets all three of untrusted text, your private data and a way to send data out.**
  Enrichment runs in two passes. Pass 1 reads the post (and the web) but never sees your files or notes.
  Pass 2 sees your context but only a checked summary of the post, and has local read tools and no
  network. Built-in presets are constructed to keep that rule and start without your Claude settings, CLAUDE.md,
  hooks or allow rules, and with Claude in Chrome and claude.ai connectors off. The program refuses to start a
  custom Claude command that breaks the rule or uses a flag it does not know (only the per-pass
  `allow_private_in_read` / `allow_network_in_relate` overrides lift that), and warns when it cannot read a
  custom command at all. `unhoard doctor` verifies the real behaviour. Details in
  [docs/agent-commands.md](https://github.com/vicennial/unhoard/blob/main/docs/agent-commands.md).
- Profile pictures, photos and video are fetched by your browser from X's servers (`pbs.twimg.com`,
  `video.twimg.com`); the page's content-security policy allows nothing else. Set `[server] show_media = false`
  to turn all of that off.
- **Bookmark text is untrusted input.** It is fenced as data in the enrichment prompt, the answer is
  validated field by field before it is stored, the card page renders text without HTML, and the launch
  brief repeats the warning and fences the model-written task as derived data. Nothing starts until you press Go.
  The card page marks every URL the relate agent wrote into a task, so you can look at it before you press Go.
- The card server checks the Host header (DNS rebinding), rejects foreign Origins, and requires a custom
  header on every state-changing request.

## Layout

```
src/unhoard/
  config.py  store.py      paths, config.toml, SQLite schema
  sync.py                  X API v2 bookmarks -> SQLite
  enrich.py                two-pass prompts, context pack, command plan, strict validation
  postdata.py              avatar, counts, media and readable text from the X API objects
  agents.py  mcp.py        agent presets and the custom-command guard; memory-MCP registry and config file
  procs.py                 run a helper in its own process group (killed on timeout)
  discover.py  tomledit.py context discovery and the atomic config edit that saves it
  doctor.py                the doctor checks
  queue.py                 ranking, daily limit, Go / Later / Skip, launched sessions
  launch/                  auto | omnigent | command | print adapters, the target resolver and the brief template
  netsafe.py               HTTP openers that refuse redirects (and ignore proxies for loopback probes)
  server.py  web/          the card page (stdlib http.server + static files)
  cli.py                   the unhoard command
src/unhoard_ext/  Omnigent extension scaffold (manifest + a page that explains what is missing)
examples/schedule/         cron, Claude Code /loop, Hermes cron
```

## FAQ

### What should I do with my X (Twitter) bookmarks?

Act on a few and let the rest go. unhoard picks three a day, says why you probably saved each one, and proposes a
task your agent can do. You press Go, Later or Skip. Later brings a card back in a few days; Skip drops it for good.

### Can Claude Code or Codex act on my Twitter bookmarks?

Yes. `unhoard enrich` uses Claude Code or Codex to write the why and the tasks, and Go can start the task in any agent
CLI through the `command` adapter, or as an Omnigent session. The [agent skill](https://github.com/vicennial/unhoard/tree/main/skills/unhoard)
lets your agent install and run unhoard for you.

### Does unhoard scrape X or use my browser cookies?

No. It uses the official X API v2 with your own developer app and an OAuth 2.0 login with the `bookmark.read`
scope (read-only). See [X API access](#x-api-access).

### How do I export my Twitter bookmarks?

`unhoard sync` copies them into a local SQLite file you own (`bookmarks.sqlite3` in the data directory; `unhoard status`
shows the path). `sync --from-file` also accepts a saved X API response.

### Do I need Omnigent?

No. Omnigent gives each task a session you can watch and resume, but any agent CLI works through the `command`
adapter, and with nothing set up Go shows the brief so you can paste it anywhere.

### Will an agent run things without asking me?

No. Enrichment only reads and writes cards. Nothing starts until you press Go, and the agent that reads the
untrusted post never sees your files. See [Privacy and safety](#privacy-and-safety).

### What does it cost?

unhoard is free and Apache-2.0 licensed. Reading bookmarks through the X API typically needs a paid X API plan, and
enrichment uses your own agent's tokens (`enrich --limit 5` first to see the cost).

## Development

```bash
python3 -m unittest discover -s tests     # no network, no real agent calls
UNHOARD_LIVE=1 python3 -m unittest discover -s tests   # also runs a live smoke test against claude/codex
```

The extension-manifest test is skipped unless `omnigent` is importable in the same environment.

## Known limits (v0.1)

- The Omnigent extension tab is a scaffold; see "Coming" above.
- Re-enriching a bookmark replaces its undecided proposals; decided ones are kept.
- Only bookmarks are read: no folders, likes or lists.
