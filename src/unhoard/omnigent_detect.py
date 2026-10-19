"""Find a running Omnigent server and let the user pick the agent that Go starts.

Only loopback addresses are probed (``GET /v1/agents`` with a short timeout), so nothing leaves this machine and a
configured remote address is never contacted by setup.
"""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Mapping
from urllib.parse import urlparse

from . import netsafe

DEFAULT_API = "http://127.0.0.1:6767"
PROBE_TIMEOUT = 1.5
LOOPBACK = ("127.0.0.1", "localhost", "::1")


@dataclass
class Agent:
    id: str
    name: str
    harness: str = ""


@dataclass
class Detection:
    api: str = ""  # the loopback address that answered; empty when none did
    agents: list[Agent] = field(default_factory=list)
    tried: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # configured addresses that are not loopback

    @property
    def running(self) -> bool:
        return bool(self.api)


def is_loopback(api: str) -> bool:
    try:
        parts = urlparse(api)
        return parts.scheme in ("http", "https") and (parts.hostname or "") in LOOPBACK
    except ValueError:
        return False


def candidate_apis(configured: str = "", env: Mapping[str, str] | None = None) -> list[str]:
    """Addresses to probe, in order: $OMNIGENT_API, the configured one, the default. No duplicates."""
    env = os.environ if env is None else env
    out: list[str] = []
    for value in (env.get("OMNIGENT_API", ""), configured, DEFAULT_API):
        value = (value or "").strip().rstrip("/")
        if value and value not in out:
            out.append(value)
    return out


def parse_agents(payload: object) -> list[Agent]:
    """Agents from a ``/v1/agents`` answer: ``{"data": [...]}`` or a bare list. Entries without an id are dropped."""
    items = payload.get("data") if isinstance(payload, dict) else payload
    agents: list[Agent] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        agent_id = item.get("id") or item.get("agent_id")
        if isinstance(agent_id, str) and agent_id:
            name = item.get("name") or item.get("display_name") or agent_id
            harness = item.get("harness")
            agents.append(Agent(agent_id, str(name), harness if isinstance(harness, str) else ""))
    return agents


def fetch_agents(api: str, timeout: float = PROBE_TIMEOUT) -> list[Agent] | None:
    """The agents on one loopback server, or None when it is unreachable or does not answer like Omnigent."""
    if not is_loopback(api):
        return None
    req = urllib.request.Request(f"{api.rstrip('/')}/v1/agents", headers={"Accept": "application/json"})
    try:
        with netsafe.loopback_opener().open(req, timeout=timeout) as resp:  # loopback only (checked above): no proxy, no redirect
            return parse_agents(json.loads(resp.read(1_000_000) or b"null"))
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
        # refused, timeout, HTTP error, not JSON, or not HTTP at all (BadStatusLine from SSH or another listener)
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
        return None


def detect(configured: str = "", env: Mapping[str, str] | None = None, timeout: float = PROBE_TIMEOUT) -> Detection:
    """Probe the candidate loopback addresses; the first one that answers with a list of agents wins."""
    result = Detection()
    for api in candidate_apis(configured, env):
        if not is_loopback(api):
            result.skipped.append(api)
            continue
        result.tried.append(api)
        agents = fetch_agents(api, timeout)
        if agents is not None:
            result.api, result.agents = api, agents
            break
    return result


def describe(found: Detection) -> str:
    """The numbered list of agents, one per line."""
    return "\n".join(f"  {n}. {a.name}" + (f" ({a.harness})" if a.harness else "") for n, a in enumerate(found.agents, 1))


def config_lines(api: str, agent: Agent, adapter: str = "") -> str:
    """The lines that make Go start ``agent``. With the default adapter ("auto") only ``[omnigent]`` is needed."""
    head = f'[launch]\nadapter = {json.dumps(adapter)}\n' if adapter else ""
    return f'{head}[omnigent]\napi = {json.dumps(api)}\nagent_id = {json.dumps(agent.id)}'


def not_running_message(found: Detection) -> str:
    where = ", ".join(found.tried) or DEFAULT_API
    text = f"Omnigent is not running (nothing answered at {where})."
    if found.skipped:
        text += f" Not probed because they are not on this machine: {', '.join(found.skipped)}."
    return text


def pick_agent(found: Detection, ask: Callable[[str], str], print_=print) -> Agent | None:
    """Show the agents and let the user choose by number. Blank (or three wrong answers) picks nothing."""
    print_(f"Omnigent is running at {found.api} with {len(found.agents)} agent(s):\n{describe(found)}")
    if not found.agents:
        print_("No agent is registered there yet. Add one in Omnigent, then run: unhoard agents")
        return None
    for _ in range(3):
        answer = ask(f"Agent that Go starts (1-{len(found.agents)}, blank = choose later): ").strip()
        if not answer:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(found.agents):
            return found.agents[int(answer) - 1]
        print_(f"Type a number from 1 to {len(found.agents)}, or press Enter to choose later.")
    return None


def print_unattended(found: Detection, print_=print, adapter: str = "auto") -> None:
    """No terminal or --yes: nobody can choose, so show the agents and the exact lines to set."""
    print_(f"Omnigent is running at {found.api} with {len(found.agents)} agent(s):\n{describe(found)}")
    if found.agents:
        print_("Nothing was chosen. To start tasks in Omnigent when you press Go, put this in the config "
              f"(use the id of the agent you want):\n"
              f"{config_lines(found.api, found.agents[0], 'auto' if adapter == 'print' else '')}\n"
              + "\n".join(f"  # {a.name}: agent_id = {json.dumps(a.id)}" for a in found.agents))
        print_("Or run in a terminal: unhoard agents")


def setup_step(adapter: str, interactive: bool, ask: Callable[[str], str], env: Mapping[str, str] | None = None,
               print_=print) -> tuple[str, dict[str, str]]:
    """The Omnigent part of ``setup``: returns the adapter to write and string keys to set under ``[omnigent]``.

    The default adapter is "auto", which uses Omnigent when it runs and an agent is chosen, so picking an agent is
    all it takes: the adapter stays "auto". Omnigent not running: nothing changes (a message only when the user chose
    the omnigent adapter). Running and nobody can answer: the agents and the exact lines are printed, nothing is
    chosen. Running and a person is there: pick an agent by number (asking first when the adapter was "print").
    """
    if adapter == "command":
        return adapter, {}
    found = detect(env=env)
    if not found.running:
        if adapter == "omnigent":
            print_(not_running_message(found) + " Go will report an error until it runs. Start Omnigent, then run: "
                   "unhoard agents (lists the agents and saves your pick).")
        return adapter, {}
    if not interactive:
        print_unattended(found, print_, adapter)
        return adapter, {}
    if adapter == "print":
        answer = ask(f"Omnigent is running at {found.api}. Start tasks there when you press Go? [y/N]: ").strip().lower()
        if not answer.startswith("y"):
            return adapter, {}
        adapter = "auto"  # an explicit "print" would ignore the agent that is about to be chosen
    agent = pick_agent(found, ask, print_)
    values = {"api": found.api}
    if agent:
        values["agent_id"] = agent.id
    else:
        print_("No agent chosen. Run: unhoard agents")
    return adapter, values
