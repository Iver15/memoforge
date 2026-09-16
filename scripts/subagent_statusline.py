#!/usr/bin/env python
"""`subagentStatusLine` script — layer D of ТЗ §7.2: one status line per memoforge subagent.

The host feeds a JSON document with `tasks[]` on stdin and takes one `{"id", "content"}` object per
line back. This script reads **nothing else**: no `state.json`, no journal, no disk at all. Its only
input is the `description`/`label` the orchestrator already puts on every `Agent` call (layer A,
`P<n>/<N> · <agent> · <label>`), so a task that is not ours is skipped and keeps the host's default
line. Anything unexpected — no stdin, malformed JSON, a missing `startTime` — ends as no output and
exit 0; a status line is decoration and must never interfere with a run.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import time

PROGRESS_PREFIX = re.compile(r"^P\d+/\d+\s+·")
"""Layer A format of §7.2: `P<n>/<N> · <agent> · <label>`."""

SEPARATOR = " · "

TASKS_KEY = "tasks"
LABEL_KEYS: tuple[str, ...] = ("description", "label")


def progress_text(task: dict) -> str | None:
    """The first of `description`/`label` written in the layer A format, or None."""
    for key in LABEL_KEYS:
        value = task.get(key)
        if isinstance(value, str) and PROGRESS_PREFIX.match(value.strip()):
            return value.strip()
    return None


def start_epoch(value: object) -> float | None:
    """`startTime` as a POSIX timestamp: epoch seconds, epoch milliseconds or an ISO-8601 string."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number / 1000.0 if number > 1e11 else number
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.timestamp()


def format_elapsed(seconds: float) -> str:
    """`45s`, `2m05s`, `1h07m` — short enough to sit at the end of a status line."""
    total = int(max(0.0, seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


def build_line(task: object, now: float) -> dict | None:
    """`{"id", "content"}` for one memoforge task, or None when the task is not ours."""
    if not isinstance(task, dict):
        return None
    text = progress_text(task)
    if text is None:
        return None
    identifier = task.get("id")
    if not isinstance(identifier, str) or not identifier:
        return None
    started = start_epoch(task.get("startTime"))
    content = text if started is None else text + SEPARATOR + format_elapsed(now - started)
    return {"id": identifier, "content": content}


def build_lines(payload: object, now: float | None = None) -> list:
    """Every status line this payload produces, in the order the host sent the tasks."""
    if not isinstance(payload, dict):
        return []
    tasks = payload.get(TASKS_KEY)
    if not isinstance(tasks, list):
        return []
    moment = time.time() if now is None else now
    lines = []
    for task in tasks:
        line = build_line(task, moment)
        if line is not None:
            lines.append(line)
    return lines


def render(payload: object, now: float | None = None) -> str:
    """The whole stdout of one tick: one JSON object per line (empty when nothing matched)."""
    return "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in build_lines(payload, now))


def main(stdin_text: str | None = None, now: float | None = None) -> str:
    """Return what the script prints; never raises, so the caller always exits 0."""
    try:
        if stdin_text is None:
            stdin_text = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        if not stdin_text.strip():
            return ""
        return render(json.loads(stdin_text), now)
    except BaseException:  # noqa: BLE001 — a status line must never fail loudly (§7.2 D)
        return ""


def _utf8_stdout() -> None:
    """CONVENTIONS: a hook may print a path or a host that the console codepage cannot encode."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass


if __name__ == "__main__":
    _utf8_stdout()
    sys.stdout.write(main())
    sys.exit(0)
