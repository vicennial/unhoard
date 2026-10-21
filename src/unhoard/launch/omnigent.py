"""Adapter that starts an Omnigent session through the server's REST API."""
from __future__ import annotations

import contextlib
import http.client
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

from .. import netsafe, omnigent_detect
from .brief import render_brief


DEFAULT_WORKSPACE = "unhoard-tasks"  # in the home folder, when the host is this machine


def user_message(text: str) -> dict:
    return {"type": "message", "data": {"role": "user", "content": [{"type": "input_text", "text": text}]}}


class OmnigentLauncher:
    """POST {api}/v1/sessions with an already-registered ``agent_id`` and the brief as first message."""

    def __init__(self, config: dict, timeout: int = 60):
        self.config = dict(config)
        self.timeout = timeout

    def request_body(self, card: dict, note: str) -> dict:
        from . import LaunchError
        c = self.config
        if not c.get("agent_id"):
            raise LaunchError("no Omnigent agent chosen: run `unhoard agents` to list your agents and pick one, "
                              "or set [omnigent] agent_id (the ids are at <api>/v1/agents)")
        body: dict = {"agent_id": c["agent_id"], "title": card["title"][:80]}
        # Optional passthroughs; names match the server's session-create request.
        for src, dst in (("host_id", "host_id"), ("workspace", "workspace"), ("harness", "harness_override"),
                         ("model", "model_override"), ("reasoning_effort", "reasoning_effort")):
            if c.get(src):
                body[dst] = c[src]
        return body

    def _call(self, api: str, method: str, path: str, body: dict | None = None) -> dict:
        from . import LaunchError
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{api}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            # no redirects (the brief and the session go only where [omnigent] api says); a loopback server is reached
            # without any proxy from the environment, as the probe does
            opener = netsafe.loopback_opener() if omnigent_detect.is_loopback(api) else netsafe.strict_opener()
            with opener.open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            err = LaunchError(f"Omnigent returned HTTP {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}")
            err.status = exc.code
            raise err from exc
        except (OSError, json.JSONDecodeError, http.client.HTTPException) as exc:  # OSError covers URLError and resets
            raise LaunchError(f"could not reach Omnigent at {api}: {exc}") from exc

    def project_id(self, api: str) -> str | None:
        """Id of the configured project (a sidebar folder), created on first use. None when unset."""
        name = str(self.config.get("project") or "").strip()
        if not name:
            return None

        def find() -> str | None:
            listed = self._call(api, "GET", "/v1/projects").get("data") or []
            return next((p.get("id") for p in listed if isinstance(p, dict) and p.get("name") == name), None)

        found = find()
        if found:
            return found
        from . import LaunchError
        try:
            return self._call(api, "POST", "/v1/projects", {"name": name}).get("id") or find()
        except LaunchError as exc:
            if "HTTP 409" in str(exc):  # created by a concurrent Go
                return find()
            raise

    def pick_host(self, api: str, body: dict) -> None:
        """Bind the session to a host when none is configured; a session without one never runs.

        One online host is used as is. With several, the one named like this machine is used, but only for a
        loopback server. Sandbox hosts are left out, as Omnigent's own pickers do. The workspace defaults to
        ~/unhoard-tasks only when the host is this machine; the agent reads untrusted post text, so it does not get
        your whole home folder unless you set ``workspace`` to it.
        """
        from . import LaunchError
        if body.get("host_id"):
            return
        try:
            listed = self._call(api, "GET", "/v1/hosts").get("hosts")
        except LaunchError as exc:
            if getattr(exc, "status", None) == 404:  # a server without hosts: it runs the session itself
                return
            raise
        online = [h for h in listed or [] if isinstance(h, dict) and h.get("status") == "online" and h.get("host_id")
                  and not h.get("sandbox_provider")]
        if len(online) > 1 and omnigent_detect.is_loopback(api):
            online = [h for h in online if h.get("name") == socket.gethostname()]
        if len(online) != 1:
            why = "no host is online" if not listed else f"{len(online) or 'none'} of its hosts could be the one"
            raise LaunchError(f"Omnigent cannot pick a host ({why}): set [omnigent] host_id and workspace "
                              f"(the ids are at {api}/v1/hosts)")
        host = online[0]
        body["host_id"] = host["host_id"]
        if not body.get("workspace"):
            if not (omnigent_detect.is_loopback(api) and host.get("name") == socket.gethostname()):
                raise LaunchError(f"[omnigent] workspace is needed: host {host.get('name') or host['host_id']} is not "
                                  "this machine, so give an absolute path on it")
            folder = Path.home() / DEFAULT_WORKSPACE
            folder.mkdir(exist_ok=True)
            body["workspace"] = str(folder)

    def __call__(self, card: dict, note: str = "") -> dict:
        from . import LaunchError
        api = str(self.config.get("api") or "http://127.0.0.1:6767").rstrip("/")
        body = self.request_body(card, note)
        self.pick_host(api, body)
        project = self.project_id(api)
        if project:
            body["project_id"] = project
        out = self._call(api, "POST", "/v1/sessions", body)
        session_id = out.get("session_id") or out.get("id")
        if not session_id:
            raise LaunchError("Omnigent did not return a session id")
        # The brief goes in as the first event, as the Omnigent app sends a first message. Given as initial_items at
        # create time it is kept as history only while the host is still starting the agent, and no turn runs.
        try:
            self._call(api, "POST", f"/v1/sessions/{session_id}/events", user_message(render_brief(card, note)))
        except LaunchError:
            with contextlib.suppress(LaunchError):  # no empty session is left behind; the card goes back
                self._call(api, "DELETE", f"/v1/sessions/{session_id}")
            raise
        return {"ref": str(session_id), "status": "started", "target": "omnigent"}
