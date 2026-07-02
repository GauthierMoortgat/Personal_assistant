"""Fable CLI — the daily rhythms as commands.

\b
    fable brief     morning briefing: the one thing, deadlines, in flight, stale
    fable add       capture a task without losing focus
    fable list      open tasks, grouped by status
    fable start     move a task to Doing
    fable done      mark a task Done
    fable stale     in-flight tasks untouched for 7+ days
    fable review    weekly review: moved / stale / open criticals
    fable capture   end-of-session: decided / learned / next action
    fable init      write a config file and explain Notion setup

\b
PM layer:
    fable delegate  hand a task to someone (@person, waiting-since date)
    fable waiting   who owes you what, with nudge flags
    fable blockers  dependency graph over the tracker — what unblocks what
    fable decide    log a decision + the why (retrievable later)
    fable rice      score a bet (reach x impact x confidence / effort) and log it
    fable log       read recent decisions/captures from the Notion log
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import click

from . import model, render
from .config import Config, load_config
from .notion import NotionError
from .render import console
from .store import Store


def _now(cfg: Config) -> datetime:
    return datetime.now(ZoneInfo(cfg.timezone))


def _store(ctx: click.Context) -> Store:
    try:
        return Store(ctx.obj)
    except NotionError as exc:
        raise click.ClickException(str(exc))


def _log_entry(ctx: click.Context, heading: str, sections: dict[str, str]) -> None:
    """Append an entry to the Notion log page, or captures.md when unconfigured."""
    cfg: Config = ctx.obj
    if cfg.log_page:
        _store(ctx).append_capture(cfg.log_page, heading, sections)
        console.print("[green]Logged to Notion.[/green]")
        return
    path = Path("captures.md")
    with path.open("a") as fh:
        fh.write(f"\n### {heading}\n")
        for label, text in sections.items():
            if text.strip():
                fh.write(f"- **{label}:** {text.strip()}\n")
    console.print(
        f"[yellow]No log_page configured — appended to {path}.[/yellow] "
        "Set FABLE_LOG_PAGE or log_page in fable.toml to log into Notion."
    )


def _resolve(tasks: list[model.Task], query: str) -> model.Task:
    matches = model.match_task(tasks, query)
    if not matches:
        raise click.ClickException(f"No open task matches '{query}'.")
    if len(matches) > 1:
        console.print(f"[yellow]'{query}' matches {len(matches)} tasks:[/yellow]")
        for t in matches:
            console.print(f"  - {t.title}  [dim]({t.status})[/dim]")
        raise click.ClickException("Be more specific.")
    return matches[0]


@click.group(help=__doc__)
@click.pass_context
def main(ctx: click.Context) -> None:
    ctx.obj = load_config()


@main.command()
@click.pass_context
def brief(ctx: click.Context) -> None:
    """Morning briefing: the one thing, hard deadlines, in flight, stale."""
    cfg: Config = ctx.obj
    store = _store(ctx)
    now = _now(cfg)
    today = now.date()

    try:
        todos = store.open_todos()
        tracker = store.open_tracker()
    except NotionError as exc:
        raise click.ClickException(str(exc))

    console.print(f"[bold]Briefing — {today.strftime('%A %d %B %Y')}[/bold]")

    top = model.pick_one_thing(todos, tracker, today)
    if top:
        render.one_thing(*top)
    else:
        console.print("\n[green]Nothing urgent. Pick from the backlog.[/green]")

    week = today + timedelta(days=7)
    deadlines = sorted(
        [t for t in todos + tracker if t.open and t.due and t.due <= week],
        key=lambda t: t.due,
    )
    if deadlines:
        render.section("Hard deadlines this week")
        console.print(render.task_table(deadlines, today, now))

    doing = [t for t in todos if t.status == "Doing"]
    criticals = [t for t in tracker if t.priority in ("Critical", "High")]
    if criticals:
        render.section("Launch tracker — critical/high open")
        console.print(render.task_table(criticals, today, now))
        leverage = model.top_leverage(tracker)
        if leverage and len(leverage[1]) > 1:
            blocker, blocked = leverage
            console.print(
                f"  [bold]Leverage:[/bold] finishing [cyan]{blocker.title}[/cyan] "
                f"unblocks {len(blocked)} tasks."
            )
    waiting = model.waiting_list(todos, now)
    if waiting:
        render.section(f"Waiting on ({len(waiting)})")
        console.print(render.waiting_table(waiting, now))
    if doing:
        render.section(f"In flight ({len(doing)})")
        console.print(render.task_table(doing[:8], today, now))

    stale = model.stale_tasks(todos, now, cfg.stale_days)
    if stale:
        render.section(f"Stale ({len(stale)} untouched ≥{cfg.stale_days}d) — close or kill")
        console.print(render.task_table(stale[:8], today, now))
    console.print()


@main.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include the tracker as well.")
@click.pass_context
def list_cmd(ctx: click.Context, show_all: bool) -> None:
    """Open tasks grouped by status."""
    cfg: Config = ctx.obj
    store = _store(ctx)
    now = _now(cfg)
    today = now.date()
    todos = store.open_todos()
    for status in ("Doing", "In review", "To do"):
        group = [t for t in todos if t.status == status]
        if group:
            render.section(f"{status} ({len(group)})")
            console.print(render.task_table(group, today, now))
    if show_all:
        tracker = store.open_tracker()
        if tracker:
            render.section(f"Launch tracker ({len(tracker)} open)")
            console.print(render.task_table(tracker, today, now))
    console.print()


@main.command()
@click.argument("title")
@click.option("--due", type=click.DateTime(formats=["%Y-%m-%d"]), help="Due date (YYYY-MM-DD).")
@click.option("-p", "--priority", type=click.Choice(["P0", "P1", "P2"]), help="Priority tag.")
@click.option("--deep/--shallow", "deep", default=None, help="Energy tag for time-slot matching.")
@click.option("-n", "--note", default="", help="Short note.")
@click.option("--to", "assignee", default=None, help="Delegate on creation (@person tag).")
@click.pass_context
def add(ctx: click.Context, title: str, due: datetime | None, priority: str | None,
        deep: bool | None, note: str, assignee: str | None) -> None:
    """Capture a task into the To Do database."""
    cfg: Config = ctx.obj
    store = _store(ctx)
    energy = None if deep is None else ("deep" if deep else "shallow")
    if assignee:
        tag = f"@{assignee.lower()} · waiting since {_now(cfg).date().isoformat()}"
        note = f"{note} · {tag}".strip(" ·") if note else tag
    task = store.add_todo(
        title, due=due.date() if due else None, notes=note, priority=priority, energy=energy
    )
    bits = " ".join(filter(None, [
        priority, energy, f"→ @{assignee.lower()}" if assignee else "",
        f"due {due.date()}" if due else "",
    ]))
    console.print(f"[green]Captured:[/green] {task.title}" + (f"  [dim]{bits}[/dim]" if bits else ""))


@main.command()
@click.argument("query")
@click.pass_context
def start(ctx: click.Context, query: str) -> None:
    """Move a task to Doing (matches by title substring)."""
    store = _store(ctx)
    task = _resolve(store.open_todos(), query)
    store.set_status(task, "Doing")
    console.print(f"[cyan]Doing:[/cyan] {task.title}")


@main.command()
@click.argument("query")
@click.pass_context
def done(ctx: click.Context, query: str) -> None:
    """Mark a task Done (matches by title substring)."""
    store = _store(ctx)
    task = _resolve(store.open_todos(), query)
    store.set_status(task, "Done")
    console.print(f"[green]Done:[/green] {task.title}")


@main.command()
@click.option("--days", default=None, type=int, help="Staleness threshold (default from config).")
@click.pass_context
def stale(ctx: click.Context, days: int | None) -> None:
    """In-flight tasks untouched for N+ days. Close them or kill them."""
    cfg: Config = ctx.obj
    store = _store(ctx)
    now = _now(cfg)
    threshold = days or cfg.stale_days
    tasks = model.stale_tasks(store.open_todos(), now, threshold)
    if not tasks:
        console.print(f"[green]Nothing stale (≥{threshold}d). Clean board.[/green]")
        return
    render.section(f"Stale ({len(tasks)} untouched ≥{threshold}d)")
    console.print(render.task_table(tasks, now.date(), now))
    console.print("\n[dim]For each: finish it, kill it, or consciously re-commit.[/dim]")


@main.command()
@click.pass_context
def review(ctx: click.Context) -> None:
    """Weekly review: what moved, what's stale, what's still critical."""
    cfg: Config = ctx.obj
    store = _store(ctx)
    now = _now(cfg)
    today = now.date()
    week_ago = now - timedelta(days=7)

    todos = store.all_todos()
    tracker = store.open_tracker()

    moved = [t for t in todos if t.status == "Done" and t.last_edited and t.last_edited >= week_ago]
    render.section(f"Shipped this week ({len(moved)})")
    if moved:
        console.print(render.task_table(moved, today, now))
    else:
        console.print("[dim]Nothing marked Done in the last 7 days.[/dim]")

    stale_list = model.stale_tasks(todos, now, cfg.stale_days)
    if stale_list:
        render.section(f"Stale ({len(stale_list)}) — decide: finish, kill, or re-commit")
        console.print(render.task_table(stale_list, today, now))

    criticals = [t for t in tracker if t.priority == "Critical"]
    if criticals:
        render.section("Still critical on the launch tracker")
        console.print(render.task_table(criticals, today, now))

    console.print("\n[bold]Next week's focus:[/bold] pick ONE. Write it down.\n")


@main.command()
@click.option("--project", default="", help="Project this session belonged to.")
@click.pass_context
def capture(ctx: click.Context, project: str) -> None:
    """End-of-session capture: what did you decide / learn / do next?"""
    cfg: Config = ctx.obj
    decided = click.prompt("What did you decide", default="", show_default=False)
    learned = click.prompt("What did you learn", default="", show_default=False)
    next_action = click.prompt("Next action", default="", show_default=False)
    if not any([decided.strip(), learned.strip(), next_action.strip()]):
        console.print("[dim]Nothing to capture.[/dim]")
        return

    now = _now(cfg)
    heading = "Session " + now.strftime("%Y-%m-%d %H:%M") + (f" — {project}" if project else "")
    sections = {"Decided": decided, "Learned": learned, "Next": next_action}
    _log_entry(ctx, heading, sections)
    if next_action.strip() and click.confirm("Add the next action as a task?", default=True):
        store = _store(ctx)
        store.add_todo(next_action.strip(), notes=f"From session capture {heading}")
        console.print(f"[green]Captured:[/green] {next_action.strip()}")


@main.command()
@click.argument("query")
@click.option("--to", "person", required=True, help="Who takes it (e.g. noah).")
@click.pass_context
def delegate(ctx: click.Context, query: str, person: str) -> None:
    """Hand an existing task to someone and start the waiting clock."""
    cfg: Config = ctx.obj
    store = _store(ctx)
    task = _resolve(store.open_todos(), query)
    store.delegate(task, person, _now(cfg).date())
    console.print(f"[cyan]Delegated to @{person.lower()}:[/cyan] {task.title}")


@main.command()
@click.option("--nudge-days", default=3, help="Flag waits at or beyond this many days.")
@click.pass_context
def waiting(ctx: click.Context, nudge_days: int) -> None:
    """Who owes you what — delegated tasks, longest wait first."""
    cfg: Config = ctx.obj
    now = _now(cfg)
    tasks = model.waiting_list(_store(ctx).open_todos(), now)
    if not tasks:
        console.print("[green]Waiting on nobody. Everything is yours.[/green]")
        return
    render.section(f"Waiting on ({len(tasks)})")
    console.print(render.waiting_table(tasks, now, nudge_days))
    overdue = [t for t in tasks if (t.wait_days(now) or 0) >= nudge_days]
    if overdue:
        console.print(f"\n[bold yellow]{len(overdue)} need a nudge today.[/bold yellow]")


@main.command()
@click.pass_context
def blockers(ctx: click.Context) -> None:
    """Dependency graph over the tracker: what unblocks what."""
    cfg: Config = ctx.obj
    now = _now(cfg)
    tracker = _store(ctx).open_tracker()
    graph, unresolved = model.blocker_graph(tracker)
    if not graph and not unresolved:
        console.print("[green]No open dependencies on the tracker.[/green]")
        return
    for blocker, blocked in sorted(graph.values(), key=lambda p: len(p[1]), reverse=True):
        render.section(f"{blocker.title}  [dim]({blocker.status}, unblocks {len(blocked)})[/dim]")
        console.print(render.task_table(blocked, now.date(), now))
    if unresolved:
        render.section("Unmatched dependencies (done, external, or too vague)")
        for task, name in unresolved:
            console.print(f"  [dim]{task.title} ← '{name}'[/dim]")
    console.print()


@main.command()
@click.argument("decision")
@click.option("--why", default="", help="The reasoning — matters as much as the outcome.")
@click.option("--project", default="", help="Project this belongs to.")
@click.pass_context
def decide(ctx: click.Context, decision: str, why: str, project: str) -> None:
    """Log a decision with its reasoning."""
    cfg: Config = ctx.obj
    heading = "Decision " + _now(cfg).strftime("%Y-%m-%d") + (f" — {project}" if project else "")
    _log_entry(ctx, heading, {"Decision": decision, "Why": why})


@main.command()
@click.argument("name")
@click.option("-r", "--reach", type=float, required=True, help="People/accounts per quarter.")
@click.option("-i", "--impact", type=float, required=True,
              help="0.25 minimal / 0.5 low / 1 medium / 2 high / 3 massive.")
@click.option("-c", "--confidence", type=float, required=True, help="0-1 (e.g. 0.8).")
@click.option("-e", "--effort", type=float, required=True, help="Person-weeks.")
@click.option("--project", default="", help="Project this bet belongs to.")
@click.pass_context
def rice(ctx: click.Context, name: str, reach: float, impact: float, confidence: float,
         effort: float, project: str) -> None:
    """Score a bet with RICE and log it."""
    cfg: Config = ctx.obj
    try:
        score = model.rice_score(reach, impact, confidence, effort)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    console.print(f"\n[bold]{name}[/bold]")
    console.print(
        f"  RICE = {reach:g} × {impact:g} × {confidence:g} / {effort:g} "
        f"= [bold cyan]{score:.1f}[/bold cyan]\n"
    )
    heading = "RICE " + _now(cfg).strftime("%Y-%m-%d") + (f" — {project}" if project else "")
    _log_entry(ctx, heading, {
        "Bet": name,
        "Score": f"{score:.1f} (R {reach:g} × I {impact:g} × C {confidence:g} / E {effort:g})",
    })


@main.command("log")
@click.option("-n", "--entries", default=5, help="How many recent entries to show.")
@click.pass_context
def log_cmd(ctx: click.Context, entries: int) -> None:
    """Read recent decisions/captures back from the Notion log page."""
    cfg: Config = ctx.obj
    if not cfg.log_page:
        raise click.ClickException(
            "No log_page configured. Set FABLE_LOG_PAGE or log_page in fable.toml."
        )
    blocks = _store(ctx).read_log(cfg.log_page)
    # entries are heading + items; walk from the end, keep the last N headings
    kept: list[tuple[str, str]] = []
    headings = 0
    for kind, text in reversed(blocks):
        kept.append((kind, text))
        if kind == "heading":
            headings += 1
            if headings >= entries:
                break
    if not kept:
        console.print("[dim]Log is empty.[/dim]")
        return
    for kind, text in reversed(kept):
        if kind == "heading":
            console.print(f"\n[bold]{text}[/bold]")
        else:
            console.print(f"  {text}")
    console.print()


@main.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Write a starter config and explain the Notion setup."""
    cfg: Config = ctx.obj
    target = Path.home() / ".config" / "fable" / "config.toml"
    if cfg.source_path:
        console.print(f"Config already loaded from [bold]{cfg.source_path}[/bold].")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "# Fable config — do not commit if you put the token here\n"
            '# notion_token = "ntn_..."   # or set NOTION_API_KEY (preferred)\n'
            f'todo_db = "{cfg.todo_db}"\n'
            f'tracker_db = "{cfg.tracker_db}"\n'
            '# log_page = ""             # page ID for end-of-session captures\n'
            f'timezone = "{cfg.timezone}"\n'
            f"stale_days = {cfg.stale_days}\n"
        )
        console.print(f"Wrote starter config to [bold]{target}[/bold].")
    console.print(
        "\nNotion setup (once):\n"
        "  1. Create an internal integration at https://www.notion.so/my-integrations\n"
        "  2. Export the token:  export NOTION_API_KEY=ntn_...\n"
        "  3. In Notion, open each database (To Do, Platform Tracker) and the log page\n"
        "     → ... menu → Connections → add your integration.\n"
        "  4. Run `fable brief` to test.\n"
    )
    if cfg.notion_token:
        console.print("[green]Token found.[/green]")
    else:
        console.print("[yellow]No token found yet (NOTION_API_KEY).[/yellow]")


if __name__ == "__main__":
    sys.exit(main())
