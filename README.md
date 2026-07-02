# Fable

Personal assistant CLI for day-to-day task management. Notion is the single
source of truth — Fable reads and writes the same ✅ To Do database and
Experiment Management Platform Tracker the team already uses, so nothing
drifts.

Fable encodes the working rhythms directly as commands: morning briefing,
focus-safe capture, stale detection, end-of-session capture, weekly review.

## Setup (once)

1. Create an internal integration at <https://www.notion.so/my-integrations>
   and copy the token.
2. Export it (or run `fable init` and put it in the config file):

   ```sh
   export NOTION_API_KEY=ntn_...
   ```

3. In Notion, share the databases with the integration: open the **✅ To Do**
   database and the **Experiment Management — Platform Tracker** → `...` menu
   → Connections → add the integration. Do the same for the log page if you
   use `fable capture` (see below).
4. Install and test:

   ```sh
   pip install -e .
   fable init     # writes ~/.config/fable/config.toml with the database IDs
   fable brief
   ```

## Daily rhythm

```sh
fable brief                 # morning: THE one thing, deadlines, in flight, stale
fable add "Call Stijn re pricing" -p P0 --due 2026-07-03 --deep
fable start "naming model"  # move to Doing (title substring match)
fable done "naming model"   # mark Done
fable capture --project "Falora GTM"   # end of session: decided / learned / next
```

Weekly (Friday):

```sh
fable review                # shipped this week, stale pile, open criticals
fable stale                 # any time: in-flight tasks untouched 7+ days
```

## PM layer

A PM's day is mostly other people's work and decisions, not their own tasks.
These commands cover that:

```sh
fable delegate "onboarding wizard" --to noah   # start the waiting clock
fable add "Review audit design" --to elise     # delegate on creation
fable waiting                                  # who owes you what; nudge flags at 3d
fable blockers                                 # tracker dependency graph: what unblocks what
fable decide "Ship Segment A first" --why "39 interviews say ad-spend pain is sharpest" --project "Falora GTM"
fable rice "NL2SQL self-serve" -r 200 -i 2 -c 0.8 -e 4
fable log -n 5                                 # read recent decisions back
```

- **Delegation** is a plain-text tag in Notes (`@noah · waiting since
  2026-07-02`) — visible in Notion, no schema change. `fable waiting` sorts by
  wait time and flags anything at 3+ days as needing a nudge. The morning
  `brief` includes the same list.
- **Blockers** parses the tracker's free-text Dependencies field ("Blocked
  by: X", "After: Y") and matches it to open tasks by title/workstream token
  overlap. The brief prints the leverage line: the one task whose completion
  unblocks the most others. Unmatched dependencies are listed separately
  instead of silently dropped.
- **Decisions and RICE scores** go to the same Notion log page as session
  captures, so `fable log` is the searchable memory of *why* things were
  chosen — the part that's usually lost.

## How prioritization works

- **The one thing** in `fable brief` is picked in this order: overdue P0 →
  Critical already in progress → any P0 → unblocked Critical on the tracker →
  anything overdue → the longest-untouched in-flight task.
- **P0/P1/P2 and deep/shallow** are stored as plain-text tags in the To Do
  database's Notes field (e.g. `P0 · deep`), so the Notion schema stays
  untouched and the team's board looks the same. `fable add -p P0 --deep`
  writes them for you; typing them by hand in Notion works too.
- **Stale** = status Doing or In review, not edited in 7+ days (configurable
  via `stale_days`). The backlog (To do) is allowed to sit; work in flight is
  not.

## End-of-session capture

`fable capture` asks three questions — what did you decide, what did you
learn, what's the next action — and appends them to a Notion log page
(`log_page` in config, or `FABLE_LOG_PAGE`). Without a log page configured it
falls back to a local `captures.md`. The next action can be added as a task on
the spot.

## Config

`fable.toml` in the working directory or `~/.config/fable/config.toml`;
environment variables win over the file.

| key / env                     | default                        |
| ----------------------------- | ------------------------------ |
| `notion_token` / `NOTION_API_KEY` | —                          |
| `todo_db` / `FABLE_TODO_DB`   | the ✅ To Do database          |
| `tracker_db` / `FABLE_TRACKER_DB` | the Platform Tracker       |
| `log_page` / `FABLE_LOG_PAGE` | — (falls back to captures.md)  |
| `timezone` / `FABLE_TZ`       | Europe/Brussels                |
| `stale_days`                  | 7                              |

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The logic (priority parsing, staleness, the one-thing heuristic) lives in
`src/fable/model.py` and is fully tested without network access. Only
`src/fable/store.py` and `src/fable/notion.py` touch the Notion API.
