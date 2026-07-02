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
DELEGATE_RE = re.compile(r"@([\w-]+)")
WAITING_SINCE_RE = re.compile(r"waiting since (\d{4}-\d{2}-\d{2})")
DEP_RE = re.compile(r"(?:blocked by|after)\s*:\s*([^;\n]+)", re.IGNORECASE)
# tokens too generic to identify a task when matching dependency text to titles
STOPWORDS = {"the", "and", "for", "all", "into", "one", "per", "new", "own",
             "van", "de", "het", "een", "voor"}

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
    delegated_to: str | None = None
    waiting_since: date | None = None

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

    def wait_days(self, now: datetime) -> int | None:
        """Days since the task was handed off (falls back to last edit)."""
        if self.waiting_since:
            return (now.date() - self.waiting_since).days
        return self.days_since_edit(now)


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


def parse_delegate(*texts: str) -> str | None:
    for text in texts:
        match = DELEGATE_RE.search(text or "")
        if match:
            return match.group(1).lower()
    return None


def parse_waiting_since(*texts: str) -> date | None:
    for text in texts:
        match = WAITING_SINCE_RE.search(text or "")
        if match:
            return date.fromisoformat(match.group(1))
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
        delegated_to=parse_delegate(notes, title),
        waiting_since=parse_waiting_since(notes),
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


def waiting_list(tasks: list[Task], now: datetime) -> list[Task]:
    """Open tasks delegated to someone, longest wait first."""
    out = [t for t in tasks if t.open and t.delegated_to]
    out.sort(key=lambda t: t.wait_days(now) or 0, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Dependency graph over the tracker's free-text Dependencies field

def dependency_names(text: str) -> list[str]:
    """'Blocked by: A, B' / 'After: C' -> ['A', 'B', 'C']."""
    names: list[str] = []
    for payload in DEP_RE.findall(text or ""):
        names.extend(part.strip() for part in payload.split(",") if part.strip())
    return names


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2 and w not in STOPWORDS}


def find_blocker(tasks: list[Task], name: str) -> Task | None:
    """Match free-text dependency name to a task by substring or token overlap."""
    name_l = name.lower()
    name_tokens = _tokens(name)
    best, best_score = None, 0.0
    for task in tasks:
        title_l = task.title.lower()
        if name_l in title_l or title_l in name_l:
            score = 1.0
        elif name_tokens:
            haystack = _tokens(task.title) | _tokens(task.workstream or "")
            score = len(name_tokens & haystack) / len(name_tokens)
        else:
            score = 0.0
        if score > best_score:
            best, best_score = task, score
    return best if best_score >= 0.6 else None


def blocker_graph(tasks: list[Task]) -> tuple[dict[str, tuple[Task, list[Task]]], list[tuple[Task, str]]]:
    """Map each open blocking task to the open tasks it blocks.

    Returns (graph keyed by blocker id, unresolved (task, dependency-text) pairs
    where no open task matched — done, external, or too vague).
    """
    open_tasks = [t for t in tasks if t.open]
    graph: dict[str, tuple[Task, list[Task]]] = {}
    unresolved: list[tuple[Task, str]] = []
    for task in open_tasks:
        for name in dependency_names(task.dependencies):
            blocker = find_blocker(open_tasks, name)
            if blocker and blocker.id != task.id:
                graph.setdefault(blocker.id, (blocker, []))[1].append(task)
            else:
                unresolved.append((task, name))
    return graph, unresolved


def top_leverage(tasks: list[Task]) -> tuple[Task, list[Task]] | None:
    """The open task whose completion unblocks the most other open tasks."""
    graph, _ = blocker_graph(tasks)
    if not graph:
        return None
    return max(graph.values(), key=lambda pair: len(pair[1]))


def rice_score(reach: float, impact: float, confidence: float, effort: float) -> float:
    """RICE: reach/quarter x impact (0.25-3) x confidence (0-1) / effort (person-weeks)."""
    if effort <= 0:
        raise ValueError("Effort must be > 0")
    return reach * impact * confidence / effort
