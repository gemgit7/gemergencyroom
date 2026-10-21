# The Gem-ergency Room

A small drag-and-drop task board, now with 100% more synergy. Python standard library + SQLite, no dependencies.

- Buckets: High (max 3), Medium (max 5), Low (unlimited), and Done (grouped by the date a task was moved there)
- Drag tasks within or between buckets; priority follows the bucket
- Search tasks and Done separately (name, description, contact, due date, tags)
- Tasks have name, description, contact, due date and tags; cards collapse/expand
- Overdue tasks are flagged; double-click a card to edit it

## Run

```
python app.py
```

Open http://127.0.0.1:8000 (set `PORT` to change). Data is stored in `tasks.db` next to `app.py`
(created on first run, not committed).
