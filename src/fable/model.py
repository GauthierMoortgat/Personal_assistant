"""Task model and pure logic: priority parsing, staleness, the one-thing heuristic.

Everything in this module is network-free so it can be tested without Notion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .notion import prop_value

PRIORITY_RE = re.compile(r"\bP([012])\b")
ENERGY_RE = re.compile(r"\b(deep|shallow)\b", re.IGNORECASE)

# Statuses that count as "open" per source database
OPEN_TODO = ("To do", "Doing", "In review")
IN_FLIGHT = ("Doing", "In review", "In progress")


@dataclass
class Task:
    id: str
    url: str
    title: str
    status: str
    source: str  # "todo" | "tracker"
    due: date | None = None
    notes: str = ""
    last_edited: datetime | None = None
    priority: str | None = None  # P0/P1/P2 (todo) or Critical/High/... (tracker)
    energy: str | None = None  # deep | shallow
    workstream: str | None = None
    dependencies: str = ""
    task_id: str | None = None

    @property
    def open(self) -> bool:
        return self.status not in ("Done",)

    @property
    def blocked(self) -> bool:
        return bool(self.dependencies.strip())

    def days_since_edit(self, now: datetime) -> int | None:
        if not self.last_edited:
            return None
        return (now - self.last_edited).days


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def _parse_edited(page: dict) -> datetime | None:
    raw = page.get("last_edited_time")
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_priority(*texts: str) -> str | None:
    for text in texts:
        match = PRIORITY_RE.search(text or "")
        if match:
            return f"P{match.group(1)}"
    return None


def parse_energy(*texts: str) -> str | None:
    for text in texts:
        match = ENERGY_RE.search(text or "")
        if match:
            return match.group(1).lower()
    return None


def todo_from_page(page: dict) -> Task:
    props = page.get("properties", {})
    title = prop_value(props.get("Task")) or ""
    notes = prop_value(props.get("Notes")) or ""
    return Task(
        id=page["id"],
        url=page.get("url", ""),
        title=title,
        status=prop_value(props.get("Status")) or "To do",
        source="todo",
        due=_parse_date(prop_value(props.get("Due"))),
        notes=notes,
        last_edited=_parse_edited(page),
        priority=parse_priority(notes, title),
        energy=parse_energy(notes, title),
    )


def tracker_from_page(page: dict) -> Task:
    props = page.get("properties", {})
    return Task(
        id=page["id"],
        url=page.get("url", ""),
        title=prop_value(props.get("Task")) or "",
        status=prop_value(props.get("Status")) or "Not started",
        source="tracker",
        due=_parse_date(prop_value(props.get("Deadline"))),
        notes=prop_value(props.get("Notes")) or "",
        last_edited=_parse_edited(page),
        priority=prop_value(props.get("Priority")),
        workstream=prop_value(props.get("Workstream")),
        dependencies=prop_value(props.get("Dependencies")) or "",
        task_id=prop_value(props.get("Task ID")),
    )


def stale_tasks(tasks: list[Task], now: datetime, days: int = 7) -> list[Task]:
    """Open, in-flight tasks not touched in `days` days — the silent pile-up."""
    out = []
    for task in tasks:
        if task.status not in IN_FLIGHT:
            continue
        age = task.days_since_edit(now)
        if age is not None and age >= days:
            out.append(task)
    out.sort(key=lambda t: t.days_since_edit(now) or 0, reverse=True)
    return out


def pick_one_thing(todos: list[Task], tracker: list[Task], today: date) -> tuple[Task, str] | None:
    """The single most important task right now, with the reason it won.

    Order: overdue P0 > Critical in progress > any P0 > unblocked Critical >
    overdue anything > oldest in-flight todo.
    """
    open_todos = [t for t in todos if t.open]
    open_tracker = [t for t in tracker if t.open]

    def overdue(t: Task) -> bool:
        return t.due is not None and t.due <= today

    for candidates, reason in (
        ([t for t in open_todos if t.priority == "P0" and overdue(t)], "P0 and overdue"),
        ([t for t in open_tracker if t.priority == "Critical" and t.status == "In progress"],
         "Critical and already in progress — finish it"),
        ([t for t in open_todos if t.priority == "P0"], "P0"),
        ([t for t in open_tracker if t.priority == "Critical" and not t.blocked],
         "Critical and unblocked"),
        (sorted([t for t in open_todos + open_tracker if overdue(t)], key=lambda t: t.due),
         "overdue"),
    ):
        if candidates:
            return candidates[0], reason

    in_flight = [t for t in open_todos if t.status in IN_FLIGHT and t.last_edited]
    if in_flight:
        in_flight.sort(key=lambda t: t.last_edited)
        return in_flight[0], "longest in flight — finish or kill it"
    return None


def match_task(tasks: list[Task], query: str) -> list[Task]:
    """Case-insensitive substring match on title, open tasks only."""
    q = query.lower().strip()
    return [t for t in tasks if t.open and q in t.title.lower()]
