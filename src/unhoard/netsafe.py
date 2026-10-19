"""HTTP openers for the places that send credentials or probe loopback.

``urllib.request.urlopen`` follows redirects and copies the Authorization header to the new address, and it sends
requests (loopback ones too) through any proxy named in the environment. Neither is wanted here:

* ``strict_opener`` refuses every redirect, so a token only ever goes to the address that was asked for.
* ``loopback_opener`` also ignores proxy environment variables, so a probe of 127.0.0.1 stays on this machine.
"""
from __future__ import annotations

import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect answer is returned as an ordinary HTTP error (3xx) instead of being followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def strict_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect)


def loopback_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def credential_url_problem(url: str) -> str | None:
    """Why a credential must not be sent to ``url``, or None. Only https, or http to this machine, is allowed."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")):
        return None
    return (f"refusing to send credentials to {parts.scheme or '?'}://{parts.hostname or '?'}: "
            "[x] api_base must be an https URL")
