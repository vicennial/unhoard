"""Launch adapters: start work for an approved card.

Every adapter has the same shape, ``launch(card, note) -> {"ref": ..., "status": ...}``,
and raises on failure (the queue then rolls the card back to "proposed").
``make_launcher(config)`` returns the adapter chosen by ``[launch] adapter``.

The default adapter is ``auto``: Omnigent when an ``[omnigent] agent_id`` is configured and the Omnigent API
answers on this machine at the moment of the launch, otherwise ``print``. ``resolve_target`` is the one place that
decides, so the card page, the checklist, ``status`` and the launch itself always agree.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..config import Config
from .brief import render_brief

Launcher = Callable[[dict, str], dict]

AUTO = "auto"
ADAPTERS = (AUTO, "print", "command", "omnigent")
AUTO_PROBE_TIMEOUT = 0.5  # seconds: a loopback server answers at once, so a slow one counts as not there


class LaunchError(RuntimeError):
    """The adapter could not start the work."""


class TargetChanged(LaunchError):
    """The page showed one target (say "Show brief") but Go would now do another (an Omnigent session), or the other
    way round. Nothing was started; the page must reload and show the right button."""

    def __init__(self, shown: str, now: str):
        super().__init__(f"launch target changed (the page showed {shown}, it is now {now}): reload the page")
        self.shown, self.now = shown, now


@dataclass(frozen=True)
class Target:
    """What Go will do. ``effective`` is "print", "command" or "omnigent"; ``configured`` is the setting."""
    configured: str
    effective: str
    reason: str
    omnigent_found: bool | None = None  # only probed for "auto"; None when it was not asked
    agent_set: bool = False

    @property
    def go_label(self) -> str:
        """The text of the card page's button: Go starts something; with print it only shows the brief."""
        return "Show brief" if self.effective == "print" else "Go"


def probe_omnigent(api: str) -> bool:
    """True when an Omnigent server answers on this machine at ``api``. Loopback only; never raises."""
    from .. import omnigent_detect
    if not omnigent_detect.is_loopback(api):
        return False
    return omnigent_detect.fetch_agents(api, timeout=AUTO_PROBE_TIMEOUT) is not None


def resolve_target(cfg: Config) -> Target:
    """Decide what Go does now. An explicit adapter always wins; ``auto`` looks at the agent and the server."""
    configured = str(cfg.get("launch", "adapter", AUTO) or AUTO)  # an empty value means the default
    if configured != AUTO:
        return Target(configured, configured, "set in [launch] adapter")
    api = str(cfg.get("omnigent", "api", "") or "http://127.0.0.1:6767")
    agent_set = bool(str(cfg.get("omnigent", "agent_id", "") or "").strip())
    found = probe_omnigent(api)
    if found and agent_set:
        return Target(AUTO, "omnigent", f"Omnigent answers at {api} and an agent is chosen", True, True)
    if found:
        return Target(AUTO, "print", f"Omnigent answers at {api} but no agent is chosen (run: unhoard agents)",
                      True, False)
    if agent_set:
        return Target(AUTO, "print", f"an agent is chosen but Omnigent does not answer at {api}", False, True)
    return Target(AUTO, "print", "no Omnigent found on this machine", False, False)


def _build(name: str, cfg: Config) -> Launcher:
    if name == "omnigent":
        from .omnigent import OmnigentLauncher
        return OmnigentLauncher(cfg.section("omnigent"))
    if name == "command":
        from .command import CommandLauncher
        return CommandLauncher(cfg.section("command"), cfg.paths.data_dir / "launch-logs")
    if name == "print":
        from .printer import PrintLauncher
        return PrintLauncher()
    raise LaunchError(f"unknown launch adapter {name!r}; choose one of {', '.join(ADAPTERS)}")


class AutoLauncher:
    """Resolves the target again on every Go, so a server that started or stopped since the page loaded is seen.

    ``expect`` is the target the page showed on its button. When the target is now another one, nothing is started
    (``TargetChanged``). Without ``expect`` (the command line, an older page) a chosen agent plus an Omnigent that
    does not answer is an error, not a silent print: the card goes back to the queue.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def __call__(self, card: dict, note: str = "", expect: str | None = None) -> dict:
        target = resolve_target(self.cfg)
        if expect and expect != target.effective:
            raise TargetChanged(expect, target.effective)
        if not expect and target.effective == "print" and target.agent_set:
            raise LaunchError(f"an Omnigent agent is chosen but Omnigent does not answer ({target.reason}); start "
                              "Omnigent and try again, or set [launch] adapter = \"print\" to only show the brief")
        return _build(target.effective, self.cfg)(card, note)


def make_launcher(cfg: Config) -> Launcher:
    name = cfg.get("launch", "adapter", AUTO) or AUTO
    if name == AUTO:
        return AutoLauncher(cfg)
    return _build(name, cfg)


__all__ = ["ADAPTERS", "AUTO", "AutoLauncher", "LaunchError", "Launcher", "Target", "TargetChanged", "make_launcher",
           "probe_omnigent", "render_brief", "resolve_target"]
