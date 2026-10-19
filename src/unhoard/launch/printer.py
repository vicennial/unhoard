"""Adapter that only prints the brief: nothing is started, you copy it where you like."""
from __future__ import annotations

import sys

from .brief import render_brief


class PrintLauncher:
    def __init__(self, stream=None):
        self.stream = stream

    def __call__(self, card: dict, note: str = "") -> dict:
        brief = render_brief(card, note)
        print(brief, file=self.stream or sys.stdout, flush=True)
        # the brief goes back to the caller too: the card page shows it, so "Show brief" shows it where you are looking
        return {"ref": f"printed:{card['action_id']}", "status": "printed", "target": "print", "brief": brief}
