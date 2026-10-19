# Card page API

The card page (`unhoard serve`) reads `GET /api/cards[?more=1]` and answers cards with
`POST /api/cards/<id>/decide` (needs the `X-Unhoard: 1` header; body `{"decision": "go|later|skip", "note": "...",
"expect_target": "omnigent|command|print"}`; `expect_target` is optional and only read for `go`, see below).

`GET /api/cards` returns `ok`, `picks` (today's cards), `waiting`, `launched`, `labels`, `later_count`,
`decided_today`, `daily_cards` and `launch`. Every card in `picks`, `waiting` and `launched` has:

| Field | Meaning |
|---|---|
| `action_id`, `title`, `brief`, `label`, `priority`, `confidence`, `why_saved`, `summary` | The proposed task. Model-written. |
| `author_username`, `author_name`, `source_url`, `tweet_created_at`, `first_seen_at`, `text` | The post. `text` is the plain stored text. |
| `author_avatar_url` | `https://` (or `data:image/`) profile picture, large size; `null` if unknown or `[server] show_media = false`. |
| `author_verified` | `true` when X marked the author verified at the last sync; `false` otherwise or unknown. |
| `metrics` | `{replies, reposts, quotes, likes, saves, views}` (numbers or `null`); `null` when X gave none. |
| `media` | Attachments in order: `{type: photo\|video\|animated_gif, url, video_url, width, height, duration_ms, alt_text}`. `url` is the photo, or the preview image of a video or GIF. `video_url` is the best `video/mp4` (`https://` only) or `null`. Empty when media is hidden. |
| `media_url` | The first attachment's `url` (kept for older pages). |
| `text_display` | The post text for display: newlines kept, HTML unescaped, `t.co` links replaced by their display form, links to attached media removed, long-form (`note_tweet`) text used. |
| `links` | `[{url, display}]`: the expanded non-media links. |
| `relate_urls` | Up to 10 URLs found in `title`, `brief` and `why_saved`, which a model wrote after reading an untrusted post, as written (any `scheme://`, `file:///`, `www.`, `git@host:path`, `host/path`, bare domains with a common TLD). The page lists them under "Links in this task"; only `https://` ones are links. |
| `relate_urls_more` | How many further URLs were found but not listed (the page shows "+N more not shown"). |
| `launch_error` | Why the last Go failed, if it did. |

`launch` says what the first button will do, so the page can label it: `{"configured": "auto", "target": "print",
"go_label": "Show brief"}`. `configured` is `[launch] adapter` (`auto`, `print`, `command` or `omnigent`). `target` is
what Go does now: `omnigent`, `command` or `print`. With the default `auto` it is `omnigent` when `[omnigent] agent_id`
is set and the Omnigent API answers on this machine (a quick loopback `GET /v1/agents`), otherwise `print`; any other
`configured` value is also the `target`. `go_label` is `"Go"` when the target starts something (`omnigent`,
`command`) and `"Show brief"` when it only prints the brief (`print`). The answer is computed on every request, and
`auto` is decided again on every Go.

### Answering a card: `POST /api/cards/<id>/decide`

A successful answer is `{"ok": true, "action_id": ..., "status": ...}`. For Go it also has `launch_ref`,
`launch_status` and `launch_target` (`omnigent`, `command` or `print`: what actually ran). For the `print` target it
has `brief`: the full task brief as plain text, the same text that is printed to the server's stdout. The page shows
it above the cards (as text, never as HTML) with a Copy button; it is not stored anywhere else.

`expect_target` is the `launch.target` the page showed on its first button. With the default `auto` adapter, Omnigent
can start or stop between the page load and the click. When the target Go would use now is not the one the page showed,
the server answers **409** `{"ok": false, "reason": "target_changed", "message": "...", "launch": {...}}` (with the
current `launch` object) and starts nothing; the card stays open and the page reloads. The check is made before the
card is touched and again at the moment of launching. Without `expect_target` (the command line, an older page), `auto`
with an `[omnigent] agent_id` set and no answer from Omnigent is an error (**502**, the card goes back to the queue)
rather than a silent print. A Go that fails for another reason is also a 502 and the card goes back to the queue.

`launched` lists cards that were answered Go, newest first (at most 20), and adds `launch_ref` (for the
`omnigent` adapter, the session id), `launch_status` and `decided_at`.

The page's content-security policy allows images from `https:` and `data:`, video only from
`https://video.twimg.com`, and scripts and `fetch` only from the page's own origin.

## First-run checklist: `GET /api/setup`

Read-only, same Host and Origin checks as the other endpoints, nothing to send, no secret in the answer. The page uses
it for its pre-flight checklist and the header "Setup" lamp. Nothing is checked live except one thing: with the default `auto` launch adapter, the `launch` item asks the
configured loopback Omnigent address for its agent list (a short `GET`) to tell whether Go can start a session
there. No call to X or to an agent.

```json
{"ok": true, "ready": 2, "total": 6, "first_run": true, "demo": false,
 "real_synced": 0, "real_enriched": 0, "demo_cards": 0,
 "items": [{"id": "x_access", "label": "X access", "status": "todo", "next": true,
            "detail": "No token. Log in, or set X_BEARER_TOKEN (bookmark.read).",
            "command": "unhoard login x"}]}
```

| Field | Meaning |
|---|---|
| `items` | Always six, in this order: `x_access` (is there a token, and from where: `X_BEARER_TOKEN` or the config file), `user_id` (`[x] user_id` set), `agent` (`[enrich]` agent or custom commands chosen), `launch` (what Go will do: for `auto`, whether Omnigent answers and an agent is chosen; for `omnigent`, whether `agent_id` is set), `data` (real bookmarks synced and enriched), `demo` (whether the cards are the fictional samples). |
| `items[].status` | `ok`, `todo`, `warn` or `info` (a note, not a to-do: for example "no Omnigent found, so the button shows the brief"). `info` counts as ready. |
| `items[].detail` | One short sentence, written by the server from fixed text. |
| `items[].command` | The exact command to run next, or `null`. A fixed string; at most a configured Omnigent address (only when it matches a strict `http(s)://host[:port]` pattern) appears in it. |
| `items[].next` | `true` on the one step to do now: the first `todo` with a command, else the first `warn` with one. |
| `ready`, `total` | Items with status `ok`, and the number of items. |
| `real_synced`, `real_enriched` | Bookmarks whose tweet id does not belong to the demo, and how many of them are enriched. |
| `demo_cards` | Fictional demo bookmarks in the database. Demo tweet ids all start with `9000000000000000`. |
| `first_run` | `true` while `real_enriched` is 0. The page shows the checklist by itself while this is true, and hides it when it turns false (unless the visitor opened it from the Setup lamp). |
| `demo` | `true` when demo cards exist and no real enriched bookmark does: "you are looking at sample cards". |
