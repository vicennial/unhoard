# Changelog

All notable changes to unhoard are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Add a line under "Unreleased" in the same change that makes it;
[RELEASING.md](RELEASING.md) says how a release turns that section into a version.

## [Unreleased]

## [0.1.2] - 2026-10-21

### Fixed
- Go starts the agent in Omnigent. The session was created without a host, so it never ran, and the brief sat in
  its history without starting a turn. Go now binds the session to a host and sends the brief as its first message,
  as the Omnigent app does. If that message cannot be sent, the empty session is deleted and the card goes back to
  the queue.
- On macOS, `unhoard setup` and `context discover` no longer list the notes folder twice (`~/notes` and `~/Notes` are
  one folder there).
- Two unhoard processes starting at the same moment (for example the page and a scheduled sync) no longer fail
  with "database is locked" while the database is set up.

### Changed
- Without `[omnigent] host_id`, Go uses the one online Omnigent host, or with several the one named like this
  machine. `workspace` defaults to `~/unhoard-tasks` when that host is this machine. Set both to choose another
  machine or folder.
- The card page can now also be served by a reverse proxy under a path such as `/bookmarks/` (it uses relative
  URLs; the proxy must send `/bookmarks` on to `/bookmarks/`). Opening it directly works as before.

## [0.1.1] - 2026-10-19

### Added
- An Agent Skill (`npx skills add vicennial/unhoard`) and an `AGENTS.md`, so coding agents can install and run it.
- A README comparison with other X bookmark tools, and a FAQ.

### Changed
- Better package keywords, classifiers and project links on PyPI.

## [0.1.0] - 2026-10-19

First release: your agent turns saved X bookmarks into a short queue of tasks, and Go starts one.
- `unhoard sync` reads your bookmarks through the official X API (OAuth 2.0 login in the browser, no scraping).
- `unhoard enrich` has your own agent CLI write why you saved each post and a task it could do.
- The card page shows a few cards a day with Go, Later and Skip. Go starts a session in Omnigent, pipes the brief to a
  command you choose, or shows it.
- `uvx unhoard try` shows the page with fictional sample cards and touches none of your data.

[Unreleased]: https://github.com/vicennial/unhoard/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/vicennial/unhoard/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/vicennial/unhoard/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/vicennial/unhoard/releases/tag/v0.1.0
