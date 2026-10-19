"""Plain-text rendering of a card for the terminal (``unhoard next``)."""
from __future__ import annotations

import html


def excerpt(text: str | None, limit: int = 280) -> str:
    flat = " ".join(html.unescape(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def who(card: dict) -> str:
    handle = card.get("author_username")
    name = card.get("author_name") or handle or "unknown"
    return f"{name} (@{handle})" if handle else name


def card_text(card: dict) -> str:
    """Compact plain text for the terminal."""
    label = f" [{card['label']}]" if card.get("label") else ""
    return "\n".join([
        f"{card['action_id']}  {card['priority']}{label}",
        f"  Task: {card['title']}",
        f"  Why:  {card.get('why_saved') or 'unknown'}",
        f"  Post: {who(card)}: {excerpt(card['text'], 200)}",
        f"  URL:  {card['source_url']}",
        "",
        f"  {card['brief']}",
    ])
