from datetime import date, datetime, timedelta, timezone

import pytest

from fable.model import (
    Task,
    blocker_graph,
    dependency_names,
    match_task,
    parse_delegate,
    parse_energy,
    parse_priority,
    parse_waiting_since,
    pick_one_thing,
    rice_score,
    stale_tasks,
    todo_from_page,
    top_leverage,
    waiting_list,
)

NOW = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def make(title="t", status="To do", source="todo", priority=None, due=None,
         edited_days_ago=0, dependencies="", workstream=None, delegated_to=None,
         waiting_since=None):
    return Task(
        id=title, url="", title=title, status=status, source=source,
        priority=priority, due=due, dependencies=dependencies, workstream=workstream,
        delegated_to=delegated_to, waiting_since=waiting_since,
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


def test_delegation_tags():
    assert parse_delegate("P1 · @noah · waiting since 2026-07-02") == "noah"
    assert parse_delegate("no tags here") is None
    assert parse_waiting_since("@noah · waiting since 2026-06-28") == date(2026, 6, 28)


def test_waiting_list_sorted_by_wait():
    short = make("short", delegated_to="elise", waiting_since=TODAY - timedelta(days=1))
    long = make("long", delegated_to="noah", waiting_since=TODAY - timedelta(days=6))
    done = make("done", status="Done", delegated_to="noah")
    mine = make("mine")
    result = waiting_list([short, long, done, mine], NOW)
    assert [t.title for t in result] == ["long", "short"]
    assert result[0].wait_days(NOW) == 6


def test_dependency_names():
    assert dependency_names("Blocked by: Campaign naming domain model, Import flow UX") == [
        "Campaign naming domain model", "Import flow UX"
    ]
    assert dependency_names("After: Open permissions") == ["Open permissions"]
    assert dependency_names("") == []


def test_blocker_graph_on_real_tracker_shapes():
    naming = make("Redesign campaign naming domain model", source="tracker",
                  status="In progress", priority="Critical")
    imports = make("Unify import + link + add AdSets/ads into one flow", source="tracker",
                   dependencies="Blocked by: Redesign campaign naming domain model")
    crm = make("Simplify CRM connection UX", source="tracker",
               dependencies="Blocked by: Campaign naming domain model, Import flow UX")
    permissions = make("Open company/project creation to all users", source="tracker",
                       workstream="Permissions & Access", priority="Critical")
    wizard = make("Fix onboarding wizard: add ad account step + reorder CRM", source="tracker",
                  dependencies="After: Open permissions")
    tasks = [naming, imports, crm, permissions, wizard]

    graph, unresolved = blocker_graph(tasks)
    blocked_by_naming = {t.title for t in graph[naming.id][1]}
    assert blocked_by_naming == {imports.title, crm.title}
    assert {t.title for t in graph[imports.id][1]} == {crm.title}
    # 'Open permissions' matches via the Permissions & Access workstream
    assert {t.title for t in graph[permissions.id][1]} == {wizard.title}
    assert unresolved == []

    top = top_leverage(tasks)
    assert top[0].title == naming.title
    assert len(top[1]) == 2


def test_blocker_graph_unresolved_when_no_match():
    task = make("Some task", source="tracker", dependencies="Blocked by: Legal signoff")
    graph, unresolved = blocker_graph([task])
    assert graph == {}
    assert unresolved == [(task, "Legal signoff")]


def test_rice_score():
    assert rice_score(500, 2, 0.8, 4) == 200.0
    with pytest.raises(ValueError):
        rice_score(100, 1, 1, 0)
