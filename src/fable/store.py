"""Fetching and mutating tasks in Notion — the only module that talks to the network."""

from __future__ import annotations

from datetime import date

from .config import Config
from .model import Task, todo_from_page, tracker_from_page
from .notion import Notion

NOT_DONE_SORTED = [{"timestamp": "last_edited_time", "direction": "descending"}]


def _not_done_filter() -> dict:
    return {"property": "Status", "status": {"does_not_equal": "Done"}}


class Store:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.notion = Notion(cfg.notion_token)

    def open_todos(self) -> list[Task]:
        pages = self.notion.query_database(
            self.cfg.todo_db, filter=_not_done_filter(), sorts=NOT_DONE_SORTED
        )
        return [todo_from_page(p) for p in pages]

    def all_todos(self) -> list[Task]:
        pages = self.notion.query_database(self.cfg.todo_db, sorts=NOT_DONE_SORTED)
        return [todo_from_page(p) for p in pages]

    def open_tracker(self) -> list[Task]:
        pages = self.notion.query_database(
            self.cfg.tracker_db, filter=_not_done_filter(), sorts=NOT_DONE_SORTED
        )
        return [tracker_from_page(p) for p in pages]

    def add_todo(self, title: str, due: date | None = None, notes: str = "",
                 priority: str | None = None, energy: str | None = None) -> Task:
        tags = " · ".join(filter(None, [priority, energy]))
        full_notes = f"{tags} · {notes}".strip(" ·") if tags else notes
        properties: dict = {
            "Task": {"title": [{"text": {"content": title}}]},
            "Status": {"status": {"name": "To do"}},
        }
        if full_notes:
            properties["Notes"] = {"rich_text": [{"text": {"content": full_notes}}]}
        if due:
            properties["Due"] = {"date": {"start": due.isoformat()}}
        page = self.notion.create_page(self.cfg.todo_db, properties)
        return todo_from_page(page)

    def set_status(self, task: Task, status: str) -> None:
        self.notion.update_page(task.id, {"Status": {"status": {"name": status}}})

    def append_capture(self, page_id: str, heading: str, sections: dict[str, str]) -> None:
        """Append an end-of-session capture to the log page."""
        children: list[dict] = [
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": [{"text": {"content": heading}}]},
            }
        ]
        for label, text in sections.items():
            if not text.strip():
                continue
            children.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [
                            {"text": {"content": f"{label}: "}, "annotations": {"bold": True}},
                            {"text": {"content": text.strip()}},
                        ]
                    },
                }
            )
        self.notion.append_blocks(page_id, children)
