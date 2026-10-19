# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Agent power users: developers who run Omnigent, Claude Code or Codex every day and save far more X posts than
they ever reopen. They open the card page to glance at everything they saved, see why each post mattered to
them, and send one straight into an agent session. (Confirmed by the maintainer, 2026-10-19.)

## Product Purpose

Turns a bookmark graveyard into a short list of things worth doing. Every saved post becomes a card: the post,
a guess at why the user saved it, and one concrete task their own agent can start. The user answers Go
(launch a session with the brief), Later (hide for a few days) or Skip. Success is bookmarks that get acted on,
not read-later piles.

## Positioning

The user's own agent reads every bookmark against the user's own context (notes, memory folders, repos,
read-only memory servers) and proposes work tied to what they are doing now. Enrichment runs in two passes so no
single agent ever holds untrusted post text, private data and a network channel at once. Local-first: one
SQLite file, a server bound to 127.0.0.1, no hosted service.

## Operating Context

- A local page at `http://127.0.0.1:8765`, opened in the user's browser next to their agent tools. A future
  Omnigent sidebar tab is planned but blocked on Omnigent's extension API.
- Three cards a day are "today's picks"; the rest wait. Cards can be filtered by label (for example a work
  project versus personal).
- Go launches through a configured adapter: an Omnigent session, any command, or a printed brief.
- Sync and enrichment run from the CLI or a schedule; the page only reads the queue and records decisions.

## Capabilities and Constraints

- Static HTML, CSS and dependency-free JavaScript served by a Python stdlib server; no framework, no build step,
  no web fonts or third-party scripts. Post media loads from X's CDN with `referrerpolicy="no-referrer"`.
- API: `GET /api/cards` (picks, waiting, labels, counts) and `POST /api/cards/<id>/decide` (go, later, skip,
  optional note), plus read-only `GET /api/setup` (the first-run checklist: what is set up and the exact next
  command). Mutating requests require the `X-Unhoard` header.
- Card data: author name, handle and profile picture, post text (long posts too), counts, photos, video and
  GIFs, posted time, why-saved guess, task title, task brief, label, priority (high, medium, low), action ID.
  `docs/card-api.md` lists the fields.
- A "Launched" section lists cards already sent to agents, with session status.

## Brand Commitments

- Name: unhoard. Line: "Your X bookmarks aren't a graveyard anymore."
- Its own visual identity; it does not need to imitate Omnigent's UI. (Confirmed by the maintainer, 2026-10-19.)
- Apache-2.0, published by vicennial.

## Evidence on Hand

- `unhoard demo` creates fictional sample cards for screenshots; README screenshots must use them,
  never a real user's bookmarks.
- No users, testimonials, benchmarks or press yet. Do not invent any.

## Product Principles

1. Everything at a glance: all open cards on one scannable surface; detail expands in place.
2. Visual over wordy: the post, the why and the task carry the page; no explanatory filler.
3. The human decides: Go, Later and Skip are always explicit clicks; nothing launches on its own.
4. Local and private by default: nothing leaves the machine except the user's own agent calls.

## Accessibility & Inclusion

Keyboard operable (cards focusable, Enter expands), status changes announced through a live region, readable at
phone width.
