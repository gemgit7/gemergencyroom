"""Task buckets web app: stdlib HTTP server + SQLite. Run: python app.py"""
import json
import os
import sqlite3
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "tasks.db")
LIMITS = {"High": 3, "Medium": 5, "Low": None}
BUCKETS = ("High", "Medium", "Low", "Done")
lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                contact TEXT DEFAULT '',
                due TEXT DEFAULT '',
                priority TEXT NOT NULL,      -- High / Medium / Low (last priority if done)
                bucket TEXT NOT NULL,        -- High / Medium / Low / Done
                position INTEGER NOT NULL DEFAULT 0,
                done_at TEXT,
                tags TEXT NOT NULL DEFAULT '[]'  -- JSON array of strings
            )"""
        )
        cols = [r[1] for r in c.execute("PRAGMA table_info(tasks)")]
        if "tags" not in cols:  # migrate databases created before tags existed
            c.execute("ALTER TABLE tasks ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")


def all_tasks(c):
    rows = c.execute("SELECT * FROM tasks ORDER BY position, id").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d.get("tags") or "[]")
        out.append(d)
    return out


def count(c, bucket):
    return c.execute("SELECT COUNT(*) FROM tasks WHERE bucket=?", (bucket,)).fetchone()[0]


def renumber(c, bucket, ids):
    for i, tid in enumerate(ids):
        c.execute("UPDATE tasks SET position=? WHERE id=?", (i, tid))


class Error(Exception):
    def __init__(self, status, msg):
        self.status, self.msg = status, msg


def clean(data):
    """Trim leading/trailing whitespace from all text fields."""
    return {k: v.strip() if isinstance(v, str) else v for k, v in data.items()}


def norm_tags(raw):
    """Tags: list (or comma-separated string); whitespace collapsed, case-insensitive dedupe,
    empty dropped, max 30 chars each, max 10 per task. Spaces inside a tag are allowed."""
    if isinstance(raw, str):
        raw = raw.split(",")
    seen, out = set(), []
    for t in raw or []:
        t = " ".join(str(t).split())[:30].strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:10]


def create_task(data):
    data = clean(data)
    name = data.get("name") or ""
    if not name:
        raise Error(400, "Name is required")
    priority = data.get("priority", "Low")
    if priority not in LIMITS:
        raise Error(400, "Bad priority")
    with lock, db() as c:
        lim = LIMITS[priority]
        if lim is not None and count(c, priority) >= lim:
            raise Error(409, f"{priority} is at capacity (max {lim}). Not everything can be a priority.")
        pos = count(c, priority)
        c.execute(
            "INSERT INTO tasks (name, description, contact, due, priority, bucket, position, tags)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (name, data.get("description", ""), data.get("contact", ""),
             data.get("due", ""), priority, priority, pos, json.dumps(norm_tags(data.get("tags")))),
        )


def update_task(tid, data):
    data = clean(data)
    name = data.get("name") or ""
    if not name:
        raise Error(400, "Name is required")
    with lock, db() as c:
        c.execute(
            "UPDATE tasks SET name=?, description=?, contact=?, due=?, tags=? WHERE id=?",
            (name, data.get("description", ""), data.get("contact", ""), data.get("due", ""),
             json.dumps(norm_tags(data.get("tags"))), tid),
        )


def delete_task(tid):
    with lock, db() as c:
        c.execute("DELETE FROM tasks WHERE id=?", (tid,))


def move_task(data):
    """Move task `id` into `bucket`, placed before task `before` (None = end)."""
    tid, target, before = data.get("id"), data.get("bucket"), data.get("before")
    if target not in BUCKETS:
        raise Error(400, "Bad bucket")
    with lock, db() as c:
        task = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        if not task:
            raise Error(404, "No such task")
        source = task["bucket"]
        lim = LIMITS.get(target)
        if source != target and lim is not None and count(c, target) >= lim:
            raise Error(409, f"{target} is at capacity (max {lim}). Please descope something first.")

        ids = [r["id"] for r in c.execute(
            "SELECT id FROM tasks WHERE bucket=? AND id<>? ORDER BY position, id", (target, tid))]
        idx = ids.index(before) if before in ids else len(ids)
        ids.insert(idx, tid)

        if target == "Done":
            if source != "Done":
                c.execute("UPDATE tasks SET bucket='Done', done_at=? WHERE id=?",
                          (datetime.now().isoformat(timespec="seconds"), tid))
        else:
            c.execute("UPDATE tasks SET bucket=?, priority=?, done_at=NULL WHERE id=?",
                      (target, target, tid))
        renumber(c, target, ids)
        if source != target:
            rest = [r["id"] for r in c.execute(
                "SELECT id FROM tasks WHERE bucket=? ORDER BY position, id", (source,))]
            renumber(c, source, rest)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def handle_api(self, fn):
        try:
            fn()
            with db() as c:
                self.send_json(all_tasks(c))
        except Error as e:
            self.send_json({"error": e.msg}, e.status)
        except Exception as e:  # noqa: BLE001
            self.send_json({"error": str(e)}, 500)

    def do_GET(self):
        if self.path == "/api/tasks":
            with db() as c:
                return self.send_json(all_tasks(c))
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if self.path == "/icon.svg":
            with open(os.path.join(HERE, "icon.svg"), "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        self.send_error(404)

    def do_POST(self):
        if self.path == "/api/tasks":
            return self.handle_api(lambda: create_task(self.body()))
        if self.path == "/api/move":
            return self.handle_api(lambda: move_task(self.body()))
        self.send_error(404)

    def do_PUT(self):
        if self.path.startswith("/api/tasks/"):
            tid = int(self.path.rsplit("/", 1)[1])
            return self.handle_api(lambda: update_task(tid, self.body()))
        self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/tasks/"):
            tid = int(self.path.rsplit("/", 1)[1])
            return self.handle_api(lambda: delete_task(tid))
        self.send_error(404)


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8000))
    print(f"Serving on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
