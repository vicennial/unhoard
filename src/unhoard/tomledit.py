"""Set list-of-strings keys in one section of a TOML file, keeping comments and everything else as it was.

Only what ``context discover``, ``setup`` and ``login x`` need: replace ``key = [...]`` (one line or many) or
``key = "text"``, or add the key
(and the section) when it is missing. The result is parsed again and compared with the original: every other key
must be unchanged, or it raises instead of writing. ``update_file`` writes through a temporary file and
``os.replace``, so a crash or a full disk cannot leave a half-written config.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Mapping, Sequence

_KEY = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")
_HEADER = re.compile(r"^\s*\[\s*([A-Za-z0-9_.\"' -]+?)\s*\]\s*(#.*)?$")


class TomlEditError(ValueError):
    """The file could not be edited safely."""


def _depth_after(line: str, depth: int) -> int:
    """Bracket depth at the end of ``line``, ignoring brackets inside strings and comments."""
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            break
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        i += 1
    return depth


def _scan(lines: Sequence[str]) -> list[tuple]:
    """Records: ("header", name, line) and ("key", name, first_line, last_line)."""
    records: list[tuple] = []
    i = 0
    while i < len(lines):
        header = _HEADER.match(lines[i])
        key = _KEY.match(lines[i])
        if header and not key:
            records.append(("header", header.group(1).strip(), i))
        elif key:
            first = i
            depth = _depth_after(lines[i][lines[i].index("=") + 1:], 0)
            while depth > 0 and i + 1 < len(lines):
                i += 1
                depth = _depth_after(lines[i], depth)
            records.append(("key", key.group(1), first, i))
        i += 1
    return records


def format_list(items: Sequence[str]) -> str:
    return "[" + ", ".join(json.dumps(str(i), ensure_ascii=False) for i in items) + "]"


def format_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)  # a JSON string is a valid TOML basic string


def set_lists(text: str, section: str, values: Mapping[str, Sequence[str]]) -> str:
    """Return ``text`` with ``[section]`` keys set to the given string lists."""
    return _set(text, section, {k: list(v) for k, v in values.items()}, lambda v: format_list(v))


def set_strings(text: str, section: str, values: Mapping[str, str]) -> str:
    """Return ``text`` with ``[section]`` keys set to the given strings (same checks as ``set_lists``)."""
    return _set(text, section, {k: str(v) for k, v in values.items()}, format_string)


def _set(text: str, section: str, values: Mapping[str, object], render) -> str:
    lines = text.split("\n")
    records = _scan(lines)
    start = next((r[2] for r in records if r[0] == "header" and r[1] == section), None)
    if start is None:
        body = [f"{k} = {render(v)}" for k, v in values.items()]
        prefix = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        out = text + prefix + f"[{section}]\n" + "\n".join(body) + "\n"
    else:
        end = next((r[2] for r in records if r[0] == "header" and r[2] > start), len(lines))
        keys = {r[1]: r for r in records if r[0] == "key" and start < r[2] < end}
        last = max([r[3] for r in keys.values()] + [start])
        ops: list[tuple[int, int, list[str]]] = []  # (first line, end line exclusive, replacement)
        extra: list[str] = []
        for name, items in values.items():
            line = f"{name} = {render(items)}"
            if name in keys:
                ops.append((keys[name][2], keys[name][3] + 1, [line]))
            else:
                extra.append(line)
        if extra:
            ops.append((last + 1, last + 1, extra))
        for first, stop, replacement in sorted(ops, key=lambda op: op[0], reverse=True):
            lines[first:stop] = replacement
        out = "\n".join(lines)
    try:
        parsed = tomllib.loads(out)
    except tomllib.TOMLDecodeError as exc:
        raise TomlEditError(f"the edited file would not be valid TOML ({exc})") from exc
    table = parsed
    for part in section.split("."):
        table = table.get(part, {}) if isinstance(table, dict) else {}
    for name, items in values.items():
        if table.get(name) != items:
            raise TomlEditError(f"could not set {name} in [{section}]")
    # everything else must be exactly as before: apply the intended change to the original data and compare
    try:
        expected = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TomlEditError(f"the original file is not valid TOML ({exc})") from exc
    expected = copy.deepcopy(expected)
    target = expected
    for part in section.split("."):
        target = target.setdefault(part, {})
    for name, items in values.items():
        target[name] = items
    if parsed != expected:
        raise TomlEditError(f"the edit would change keys other than {', '.join(values)} in [{section}]; not written")
    return out


def write_atomic(path: Path | str, text: str) -> None:
    """Replace ``path`` with ``text`` through a temporary file in the same folder (``os.replace``), keeping the mode.

    A symlink is resolved first: the temporary file is written next to the real target and replaces the target, so a
    config that is a symlink (dotfiles managers do this) stays a symlink instead of being swapped for a plain file.
    """
    path = Path(os.path.realpath(path))
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = 0o600
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_file(path: Path | str, section: str, values: Mapping[str, Sequence[str]]) -> str:
    """Set the lists in ``[section]`` of the TOML file at ``path`` (verified, then written atomically)."""
    path = Path(path)
    new = set_lists(path.read_text(encoding="utf-8"), section, values)
    write_atomic(path, new)
    return new


def update_file_strings(path: Path | str, section: str, values: Mapping[str, str]) -> str:
    """Set the strings in ``[section]`` of the TOML file at ``path`` (verified, then written atomically)."""
    path = Path(path)
    new = set_strings(path.read_text(encoding="utf-8"), section, values)
    write_atomic(path, new)
    return new
