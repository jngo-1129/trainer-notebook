"""Account profile: local SQLite at profile/notebook.db (gitignored).

Safety: stores only what the user types or confirms from their own screenshots.
No login, tokens, cookies, account ID, or friend code are ever stored (rules 5, 8).
"""
import json
import sqlite3
from pathlib import Path

PATH = Path(__file__).parent / "profile" / "notebook.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS owned (       -- support cards (level = limit break 0-4) and umas (level = stars 1-5)
  kind TEXT CHECK (kind IN ('support','uma')), id INTEGER, level INTEGER NOT NULL,
  updated TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (kind, id));
CREATE TABLE IF NOT EXISTS veteran (     -- trained umas; sparks = [{"color","name","stars"}]
  id INTEGER PRIMARY KEY, card_id INTEGER NOT NULL, rating INTEGER, sparks TEXT NOT NULL DEFAULT '[]',
  locked INTEGER NOT NULL DEFAULT 0, note TEXT DEFAULT '', updated TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS kv (          -- inventory counts, settings, and small JSON lists (keys starting _ are internal)
  key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS imported (    -- screenshots already reviewed, so re-import only picks up new ones
  file TEXT PRIMARY KEY, mtime REAL, done TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS history (     -- change log for backups / undo by hand
  id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP, tbl TEXT, key TEXT, old TEXT, new TEXT);
CREATE TABLE IF NOT EXISTS run (         -- finished careers: stats/deck/parents as JSON
  id INTEGER PRIMARY KEY, date TEXT DEFAULT (date('now')), uma_id INTEGER, distance TEXT, rating INTEGER,
  stats TEXT DEFAULT '{}', deck TEXT DEFAULT '[]', parents TEXT DEFAULT '[]', note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS pull_log (    -- gacha sessions: how many pulls, what came out
  id INTEGER PRIMARY KEY, date TEXT DEFAULT (date('now')), banner TEXT, kind TEXT, pulls INTEGER,
  ssr INTEGER DEFAULT 0, target INTEGER DEFAULT 0, rate REAL DEFAULT 0.0075, note TEXT DEFAULT '');
"""
VET_FLAGS = ("locked", "pinned", "lineup", "rental")  # locked = locked in game; pinned = protected in this app


def conn(path: Path = PATH) -> sqlite3.Connection:
    path.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(veteran)")}
    for col in set(VET_FLAGS) - cols:  # migrate older profiles
        c.execute(f"ALTER TABLE veteran ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
    return c


def _log(c, tbl: str, key, old, new):
    if not str(key).startswith("_"):
        c.execute("INSERT INTO history(tbl,key,old,new) VALUES(?,?,?,?)",
                  (tbl, str(key), None if old is None else json.dumps(old), None if new is None else json.dumps(new)))


def profile(c: sqlite3.Connection) -> dict:
    vets = [dict(r) | {"sparks": json.loads(r["sparks"])} for r in c.execute("SELECT * FROM veteran ORDER BY id")]
    return {
        "owned": [dict(r) for r in c.execute("SELECT * FROM owned ORDER BY kind, id")],
        "veterans": vets,
        "kv": {r["key"]: r["value"] for r in c.execute("SELECT * FROM kv")},
    }


def get_json(c, key: str, default):
    r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    try:
        return json.loads(r["value"]) if r and r["value"] else default
    except ValueError:
        return default


def set_owned(c, kind: str, cid: int, level: int):
    lo, hi = (0, 4) if kind == "support" else (1, 5)
    if not lo <= level <= hi:
        raise ValueError(f"{kind} level must be {lo}-{hi}")
    old = c.execute("SELECT level FROM owned WHERE kind=? AND id=?", (kind, cid)).fetchone()
    with c:
        c.execute("INSERT INTO owned(kind,id,level) VALUES(?,?,?) ON CONFLICT DO UPDATE "
                  "SET level=excluded.level, updated=CURRENT_TIMESTAMP", (kind, cid, level))
        _log(c, "owned", f"{kind}:{cid}", old and old["level"], level)


def del_owned(c, kind: str, cid: int):
    old = c.execute("SELECT level FROM owned WHERE kind=? AND id=?", (kind, cid)).fetchone()
    with c:
        c.execute("DELETE FROM owned WHERE kind=? AND id=?", (kind, cid))
        _log(c, "owned", f"{kind}:{cid}", old and old["level"], None)


def set_kv(c, key: str, value):
    old = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    with c:
        c.execute("INSERT INTO kv VALUES(?,?) ON CONFLICT DO UPDATE SET value=excluded.value", (key, str(value)))
        if not old or old["value"] != str(value):
            _log(c, "kv", key, old and old["value"], str(value))


def save_veteran(c, v: dict) -> int:
    """Insert (no id) or update a veteran record in this app's DB. Returns its id."""
    for s in v.get("sparks", []):
        if s.get("color") not in ("blue", "pink", "green", "white") or not 1 <= int(s.get("stars", 0)) <= 3:
            raise ValueError(f"bad spark {s}")
    row = (v["card_id"], v.get("rating"), json.dumps(v.get("sparks", [])), v.get("note", ""),
           *(int(bool(v.get(f))) for f in VET_FLAGS))
    cols = "card_id, rating, sparks, note, " + ", ".join(VET_FLAGS)
    with c:
        if v.get("id"):
            old = c.execute("SELECT * FROM veteran WHERE id=?", (v["id"],)).fetchone()
            c.execute(f"UPDATE veteran SET ({cols}, updated) = ({', '.join('?' * len(row))}, CURRENT_TIMESTAMP) "
                      "WHERE id=?", (*row, v["id"]))
            _log(c, "veteran", v["id"], old and dict(old), v)
            return v["id"]
        vid = c.execute(f"INSERT INTO veteran({cols}) VALUES({', '.join('?' * len(row))})", row).lastrowid
        _log(c, "veteran", vid, None, v)
        return vid


def set_vet_flag(c, vid: int, flag: str, on: bool):
    if flag not in VET_FLAGS:
        raise ValueError("unknown flag")
    with c:
        c.execute(f"UPDATE veteran SET {flag}=? WHERE id=?", (int(on), vid))
        _log(c, "veteran", f"{vid}.{flag}", None, int(on))


def del_veteran(c, vid: int):
    """Removes the record from this app only. The app never deletes anything in the game."""
    old = c.execute("SELECT * FROM veteran WHERE id=?", (vid,)).fetchone()
    with c:
        c.execute("DELETE FROM veteran WHERE id=?", (vid,))
        _log(c, "veteran", vid, old and dict(old), None)


def mark_imported(c, file: str, mtime: float):
    with c:
        c.execute("INSERT OR REPLACE INTO imported(file,mtime) VALUES(?,?)", (file, mtime))


def imported(c) -> dict[str, float]:
    return {r["file"]: r["mtime"] for r in c.execute("SELECT * FROM imported")}


# ---------- run log / pull log ----------

def add_row(c, table: str, row: dict) -> int:
    cols = {"run": ("date", "uma_id", "distance", "rating", "stats", "deck", "parents", "note"),
            "pull_log": ("date", "banner", "kind", "pulls", "ssr", "target", "rate", "note")}[table]
    vals = {k: json.dumps(row[k]) if isinstance(row.get(k), (dict, list)) else row.get(k) for k in cols if row.get(k) is not None}
    with c:
        rid = c.execute(f"INSERT INTO {table}({', '.join(vals)}) VALUES({', '.join('?' * len(vals))})",
                        tuple(vals.values())).lastrowid
        _log(c, table, rid, None, vals)
        return rid


def rows(c, table: str) -> list[dict]:
    assert table in ("run", "pull_log", "history")
    out = [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id DESC")]
    for r in out:
        for k in ("stats", "deck", "parents"):
            if k in r and isinstance(r[k], str):
                r[k] = json.loads(r[k] or "null")
    return out


def del_row(c, table: str, rid: int):
    assert table in ("run", "pull_log")
    with c:
        c.execute(f"DELETE FROM {table} WHERE id=?", (rid,))
        _log(c, table, rid, "row", None)


# ---------- backup ----------

TABLES = ("owned", "veteran", "kv", "imported", "run", "pull_log", "history")


def export(c) -> dict:
    return {"format": "trainer-notebook-backup-1", "tables": {t: [dict(r) for r in c.execute(f"SELECT * FROM {t}")] for t in TABLES}}


def restore(c, data: dict):
    """Replace the whole profile with a backup made by export(). All or nothing."""
    if data.get("format") != "trainer-notebook-backup-1":
        raise ValueError("not a Trainer Notebook backup")
    with c:
        for t in TABLES:
            cols = {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
            c.execute(f"DELETE FROM {t}")
            for row in data["tables"].get(t, []):
                row = {k: v for k, v in row.items() if k in cols}
                if row:
                    c.execute(f"INSERT INTO {t}({', '.join(row)}) VALUES({', '.join('?' * len(row))})", tuple(row.values()))
