# AGENTS.md

Notes for coding agents working in this repository. Users who want unhoard installed and running: use the agent skill
in `skills/unhoard/SKILL.md` (`npx skills add vicennial/unhoard`) or the prompt in the README.

## What this is

unhoard turns a user's X (Twitter) bookmarks into a short queue of tasks their agent can start. Python 3.11+, standard
library only. `README.md` has the commands and configuration; the layout is in its "Layout" section.

## Test

```bash
python3 -m unittest discover -s tests     # no network, no real agent calls
```

Run the full suite before you hand back a change. `UNHOARD_LIVE=1` also runs a live smoke test against claude/codex.

## Rules that the code depends on

- **No runtime dependencies.** `dependencies = []` in `pyproject.toml` stays empty; only `src/unhoard_ext` may import
  omnigent.
- **The two-pass rule.** No agent gets untrusted post text, the user's private context and a way to send data out at
  the same time. Pass 1 reads the post and the web but no private files; pass 2 sees context and a checked summary,
  with no network. Do not weaken the custom-command guard in `agents.py`.
- **Nothing starts until the user presses Go.** Never add a path that starts a task without a user decision.
- **Untrusted text.** Bookmark text is fenced as data in prompts, validated field by field, and rendered without HTML.
- **Never print or log tokens.** `status` and `doctor` show only whether a token is present and its source.
- **Loopback only.** The server binds 127.0.0.1, checks Host and Origin, and needs the `X-Unhoard` header on
  state-changing requests. Phone access goes through `[server] allowed_hosts` and `tailscale serve`, never a public
  bind.
- **Demo data is fictional.** Demo handles contain a dot so they can never be real X accounts; demo links use
  example.com.
- **No one's machine in the repo.** Use placeholders (`mybox.tail1234.ts.net`, `/home/me`); `tests/test_public_tree.py`
  checks. Settings for one person's setup go in their config, not in defaults.

## Changes and releases

Add a line under "Unreleased" in `CHANGELOG.md` with every change a user would notice. Releases: `RELEASING.md`.
