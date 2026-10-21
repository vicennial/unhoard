# Releasing unhoard

A release is a version bump, a dated changelog section and a tag. GitHub Actions does the rest: it runs the tests,
builds the package, publishes it to PyPI with Trusted Publishing (no token anywhere) and creates the GitHub Release
with the changelog section as its notes.

## Every change

- Work on a branch and open a pull request. CI runs the tests on Linux and macOS and scans for secrets.
- Add a line under `## [Unreleased]` in `CHANGELOG.md` for anything a user would notice (Added, Changed, Fixed,
  Removed, Security).

## A release

1. On `main`, in one commit named `unhoard X.Y.Z`:
   - set `__version__` in `src/unhoard/__init__.py` (`pyproject.toml` reads it) and `version=` in
     `src/unhoard_ext/plugin.py` (the Omnigent manifest may import nothing else; a test checks they match);
   - rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD`, add a new empty `## [Unreleased]` above it, and update the
     compare links at the bottom.
   Patch (`0.1.x`) for fixes, minor (`0.x.0`) for new features or changed behavior.
2. Push `main`, then the tag:

   ```bash
   git tag -a vX.Y.Z -m "unhoard X.Y.Z" && git push origin vX.Y.Z
   ```

3. Watch the "Release" workflow. It stops before publishing when the tag, `__version__` and the changelog do not agree
   or a test fails. A version that is already on PyPI is not uploaded again, so re-running the workflow is safe.
4. Upgrade where you use it: `uv tool upgrade unhoard` (or `pipx upgrade unhoard`), then restart `unhoard serve`.

PyPI never accepts the same version twice. If a release is broken, fix it and release the next patch version.

## One-time setup

- PyPI: in the project's Publishing settings, add a trusted publisher: owner `vicennial`, repository `unhoard`,
  workflow `release.yml`, environment `pypi`.

## Keeping private details out

This repo is public. `tests/test_public_tree.py` fails on real-looking tailnet names, home folders and Tailscale
addresses. A maintainer's own host names, ids and paths belong in a private pre-commit/pre-push hook on their machine,
with the word list kept outside the repo.
