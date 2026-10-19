# Claude Code `/loop`

Keep a Claude Code session open (in `tmux` or similar) and let it run the sync on an interval.
Paste this into the session:

```
/loop 6h Run `unhoard sync`, then `unhoard enrich --limit 10`, then
`unhoard status`. Reply with one line: new bookmarks, new cards, any errors.
Do not answer cards and do not run `unhoard decide`: only the user decides.
```

Notes:

- The session needs `X_BEARER_TOKEN` in its environment, and `UNHOARD_HOME` /
  `UNHOARD_CONFIG` if the session's HOME differs from your shell's.
- `/loop` runs only while the session is open. For a schedule that survives restarts use cron
  (`crontab.txt`).
- The enrichment step runs your configured agent commands (for example `claude -p`) as separate
  processes, so loop turns stay small.
