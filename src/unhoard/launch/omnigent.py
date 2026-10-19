"""Adapter that starts an Omnigent session through the server's REST API."""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request

from .. import netsafe, omnigent_detect
from .brief import render_brief


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
        body: dict = {"agent_id": c["agent_id"], "title": card["title"][:80],
                      "initial_items": [user_message(render_brief(card, note))]}
        # Optional passthroughs; names match the server's session-create request.
        for src, dst in (("host_id", "host_id"), ("workspace", "workspace"), ("harness", "harness_override"),
                         ("model", "model_override"), ("reasoning_effort", "reasoning_effort")):
            if c.get(src):
                body[dst] = c[src]
        if body.get("workspace") and not body.get("host_id"):
            raise LaunchError("[omnigent] workspace needs host_id as well (curl <api>/v1/hosts)")
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
            raise LaunchError(f"Omnigent returned HTTP {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, http.client.HTTPException) as exc:
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

    def __call__(self, card: dict, note: str = "") -> dict:
        from . import LaunchError
        api = str(self.config.get("api") or "http://127.0.0.1:6767").rstrip("/")
        body = self.request_body(card, note)
        project = self.project_id(api)
        if project:
            body["project_id"] = project
        out = self._call(api, "POST", "/v1/sessions", body)
        session_id = out.get("session_id") or out.get("id")
        if not session_id:
            raise LaunchError("Omnigent did not return a session id")
        return {"ref": str(session_id), "status": "started", "target": "omnigent"}
