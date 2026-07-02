from datetime import date, datetime, timedelta, timezone

from fable.model import (
    Task,
    match_task,
    parse_energy,
    parse_priority,
    pick_one_thing,
    stale_tasks,
    todo_from_page,
)

NOW = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def make(title="t", status="To do", source="todo", priority=None, due=None,
         edited_days_ago=0, dependencies=""):
    return Task(
        id=title, url="", title=title, status=status, source=source,
        priority=priority, due=due, dependencies=dependencies,
        last_edited=NOW - timedelta(days=edited_days_ago),
    )


def test_parse_priority_and_energy():
    assert parse_priority("P0 · call Stijn") == "P0"
    assert parse_priority("fix bug", "P2 later") == "P2"
    assert parse_priority("nothing here") is None
    assert parse_priority("APP2X") is None  # word boundary, not substring
    assert parse_energy("deep · design work") == "deep"
    assert parse_energy("Shallow admin") == "shallow"


def test_stale_only_in_flight():
    fresh = make("fresh", status="Doing", edited_days_ago=2)
    old_doing = make("old-doing", status="Doing", edited_days_ago=10)
    old_todo = make("old-todo", status="To do", edited_days_ago=30)  # backlog is not stale
    older = make("older", status="In review", edited_days_ago=20)
    result = stale_tasks([fresh, old_doing, old_todo, older], NOW, days=7)
    assert [t.title for t in result] == ["older", "old-doing"]


def test_one_thing_prefers_overdue_p0():
    p0_overdue = make("p0-overdue", priority="P0", due=TODAY - timedelta(days=1))
    critical = make("crit", source="tracker", priority="Critical", status="In progress")
    task, reason = pick_one_thing([p0_overdue], [critical], TODAY)
    assert task.title == "p0-overdue"
    assert "overdue" in reason


def test_one_thing_critical_in_progress_beats_plain_p0():
    p0 = make("p0", priority="P0")
    critical = make("crit", source="tracker", priority="Critical", status="In progress")
    task, _ = pick_one_thing([p0], [critical], TODAY)
    assert task.title == "crit"


def test_one_thing_skips_blocked_criticals():
    blocked = make("blocked", source="tracker", priority="Critical",
                   status="Not started", dependencies="Blocked by: naming model")
    free = make("free", source="tracker", priority="Critical", status="Not started")
    task, reason = pick_one_thing([], [blocked, free], TODAY)
    assert task.title == "free"
    assert reason == "Critical and unblocked"


def test_one_thing_falls_back_to_longest_in_flight():
    a = make("newer", status="Doing", edited_days_ago=3)
    b = make("oldest", status="Doing", edited_days_ago=12)
    task, reason = pick_one_thing([a, b], [], TODAY)
    assert task.title == "oldest"
    assert "in flight" in reason


def test_one_thing_none_when_empty():
    assert pick_one_thing([], [], TODAY) is None


def test_match_task_open_only_substring():
    tasks = [
        make("Audit herdesignen", status="Doing"),
        make("Audit checken", status="Done"),
    ]
    assert [t.title for t in match_task(tasks, "audit")] == ["Audit herdesignen"]


def test_todo_from_page_parses_notion_payload():
    page = {
        "id": "abc",
        "url": "https://notion.so/abc",
        "last_edited_time": "2026-06-20T08:00:00.000Z",
        "properties": {
            "Task": {"type": "title", "title": [{"plain_text": "Ship the brief"}]},
            "Status": {"type": "status", "status": {"name": "Doing"}},
            "Due": {"type": "date", "date": {"start": "2026-07-03"}},
            "Notes": {"type": "rich_text", "rich_text": [{"plain_text": "P1 · deep"}]},
        },
    }
    task = todo_from_page(page)
    assert task.title == "Ship the brief"
    assert task.status == "Doing"
    assert task.due == date(2026, 7, 3)
    assert task.priority == "P1"
    assert task.energy == "deep"
    assert task.days_since_edit(NOW) == 12
