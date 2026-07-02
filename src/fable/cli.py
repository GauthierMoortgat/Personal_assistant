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
@click.pass_context
def add(ctx: click.Context, title: str, due: datetime | None, priority: str | None,
        deep: bool | None, note: str) -> None:
    """Capture a task into the To Do database."""
    store = _store(ctx)
    energy = None if deep is None else ("deep" if deep else "shallow")
    task = store.add_todo(
        title, due=due.date() if due else None, notes=note, priority=priority, energy=energy
    )
    bits = " ".join(filter(None, [priority, energy, f"due {due.date()}" if due else ""]))
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
    heading = now.strftime("%Y-%m-%d %H:%M") + (f" — {project}" if project else "")
    sections = {"Decided": decided, "Learned": learned, "Next": next_action}

    if cfg.log_page:
        store = _store(ctx)
        store.append_capture(cfg.log_page, heading, sections)
        console.print("[green]Captured to Notion log.[/green]")
    else:
        path = Path("captures.md")
        with path.open("a") as fh:
            fh.write(f"\n### {heading}\n")
            for label, text in sections.items():
                if text.strip():
                    fh.write(f"- **{label}:** {text.strip()}\n")
        console.print(
            f"[yellow]No log_page configured — appended to {path}.[/yellow] "
            "Set FABLE_LOG_PAGE or log_page in fable.toml to capture into Notion."
        )
    if next_action.strip() and click.confirm("Add the next action as a task?", default=True):
        store = _store(ctx)
        store.add_todo(next_action.strip(), notes=f"From session capture {heading}")
        console.print(f"[green]Captured:[/green] {next_action.strip()}")


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
