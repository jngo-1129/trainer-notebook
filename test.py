"""Run every self-test: safety scan first, then the math. python test.py

Safety: runs local checks only.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

import db
import importer
import pulls
import safety_check

found = safety_check.scan_repo()
for f in found:
    print("FAIL", f)
assert not found, "safety check failed"
safety_check._selftest()
print("safety check: PASS")

pulls._selftest()

with tempfile.TemporaryDirectory() as d:
    c = db.conn(Path(d) / "t.db")
    db.set_owned(c, "support", 30120, 4)
    db.set_owned(c, "support", 30120, 2)  # upsert, not duplicate
    try:
        db.set_owned(c, "support", 1, 5)
        raise AssertionError("LB 5 accepted")
    except ValueError:
        pass
    vid = db.save_veteran(c, {"card_id": 100101, "sparks": [{"color": "blue", "name": "Speed", "stars": 3}]})
    p = db.profile(c)
    assert p["owned"][0]["level"] == 2 and len(p["owned"]) == 1 and p["veterans"][0]["id"] == vid
    c.close()
print("db selftest: PASS")

assert importer.rows([[[[0, 10], [50, 10], [50, 30], [0, 30]], "Carats", 1], [[[100, 12], [150, 12], [150, 31], [100, 31]], "1,500", 1]]) == ["Carats 1,500"]
assert importer.match(["Carats 1,500"])["numbers"][0]["value"] == 1500
print("importer selftest: PASS")

import advisor  # noqa: E402
import banners  # noqa: E402
import model  # noqa: E402

model._selftest()
advisor._selftest()
banners._selftest()

import racesim  # noqa: E402
import tools  # noqa: E402
import veterans  # noqa: E402

racesim._selftest()
tools._selftest()
veterans._selftest()

with tempfile.TemporaryDirectory() as d:  # backup round-trip + history + flags
    c = db.conn(Path(d) / "t.db")
    db.set_owned(c, "uma", 100101, 3)
    vid = db.save_veteran(c, {"card_id": 100101, "sparks": [], "pinned": True})
    db.set_vet_flag(c, vid, "lineup", True)
    backup = db.export(c)
    db.del_owned(c, "uma", 100101)
    db.restore(c, backup)
    p = db.profile(c)
    assert p["owned"][0]["level"] == 3 and p["veterans"][0]["pinned"] == 1 and p["veterans"][0]["lineup"] == 1
    assert db.rows(c, "history")
    try:
        db.restore(c, {"format": "nope"})
        raise AssertionError("bad backup accepted")
    except ValueError:
        pass
    assert db.profile(c)["owned"], "failed restore must not wipe data"
    c.close()
print("backup selftest: PASS")

import evaluate  # noqa: E402

toy = [{"card": {"type": "Speed", "id": i}, "tier": t} for i, t in enumerate([3, 2, 1])]
assert evaluate.concordance(toy, lambda x: x["tier"]) == 1.0
assert evaluate.concordance(toy, lambda x: -x["tier"]) == 0.0
assert evaluate.concordance(toy, lambda x: 0) == 0.5  # ties count half
a, b = evaluate.ols([(0, 10), (1, 12), (2, 14)])
assert abs(a - 10) < 1e-9 and abs(b - 2) < 1e-9
print("evaluate selftest: PASS")
