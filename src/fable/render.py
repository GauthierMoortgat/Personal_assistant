"""Terminal output. One place for all formatting so the CLI stays readable."""

from __future__ import annotations

from datetime import date, datetime

from rich.console import Console
from rich.table import Table

from .model import Task

console = Console()

PRIORITY_STYLE = {
    "P0": "bold red",
    "Critical": "bold red",
    "P1": "yellow",
    "High": "yellow",
    "P2": "dim",
    "Medium": "dim",
    "Low": "dim",
}


def _due_label(task: Task, today: date) -> str:
    if not task.due:
        return ""
    delta = (task.due - today).days
    if delta < 0:
        return f"[red]overdue {-delta}d[/red]"
    if delta == 0:
        return "[red]today[/red]"
    if delta == 1:
        return "[yellow]tomorrow[/yellow]"
    return task.due.strftime("%d %b")


def _priority_label(task: Task) -> str:
    if not task.priority:
        return ""
    style = PRIORITY_STYLE.get(task.priority, "")
    return f"[{style}]{task.priority}[/{style}]" if style else task.priority


def task_table(tasks: list[Task], today: date, now: datetime, title: str = "") -> Table:
    table = Table(title=title or None, show_edge=False, pad_edge=False, box=None)
    table.add_column("task", overflow="fold", max_width=60)
    table.add_column("prio", justify="left")
    table.add_column("due")
    table.add_column("status", style="dim")
    table.add_column("age", style="dim", justify="right")
    for task in tasks:
        age = task.days_since_edit(now)
        table.add_row(
            task.title or "[dim](untitled)[/dim]",
            _priority_label(task),
            _due_label(task, today),
            task.status,
            f"{age}d" if age is not None else "",
        )
    return table


def one_thing(task: Task, reason: str) -> None:
    console.print()
    console.print(f"[bold]THE ONE THING[/bold]  [dim]({reason})[/dim]")
    console.print(f"  [bold cyan]{task.title}[/bold cyan]")
    if task.notes:
        console.print(f"  [dim]{task.notes[:160]}[/dim]")
    console.print(f"  [dim]{task.url}[/dim]")


def section(title: str) -> None:
    console.print()
    console.print(f"[bold]{title}[/bold]")
