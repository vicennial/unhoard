# Hermes cron

Hermes runs scheduled jobs itself, so no system timer is needed. The sync is plain shell work, so
use a no-agent job: the script runs on schedule and Hermes only speaks up when it prints something.

1. Save `~/.hermes/scripts/bookmarks-sync.sh` (and `chmod 700` it):

   ```bash
   #!/usr/bin/env bash
   # Silent on success; any output is delivered as an alert.
   set -u
   . "$HOME/.config/unhoard/env"   # X_BEARER_TOKEN and the two UNHOARD_* paths
   out=$(unhoard sync 2>&1 && unhoard enrich --limit 10 2>&1) \
     || { echo "bookmarks sync failed: $out"; exit 0; }
   ```

2. Schedule it:

   ```bash
   hermes cron create --name bookmarks-sync --no-agent --script bookmarks-sync.sh "every 6h"
   hermes cron list
   ```

Check the options with `hermes cron create --help` for your version (delivery target, failure
notices). The env file is described in `crontab.txt`.
