"""Trainer Notebook web server: python app.py -> http://127.0.0.1:8765

Safety: runs safety_check first and refuses to start on any finding. Binds to 127.0.0.1 only.
Serves only this app's own UI, profile, and images from the user's screenshot folder.
Advisory only: no endpoint sends input to, reads from, or deletes anything in the game.
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

import advisor
import banners
import db
import gamedata
import importer
import model
import pulls
import racesim
import safety_check
import tools
import veterans

app = FastAPI(title="Trainer Notebook")
STATIC = Path(__file__).parent / "static"
PORT = 8765
LOCAL = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Block other websites open in the browser from calling this API (CSRF / DNS rebinding)."""
    origin = request.headers.get("origin", "").split("://")[-1]
    if request.headers.get("host") not in LOCAL or (origin and origin not in LOCAL):
        return PlainTextResponse("forbidden", 403)
    return await call_next(request)


def game_folder(p: Path) -> bool:
    """Refuse anything that looks like a game install/save folder (rule 4): known paths or program files."""
    p = p.resolve()
    near = [p, *list(p.parents)[:3]]  # the folder and a few parents: catches subfolders of an install too
    return bool(safety_check.PATH_RE.search(str(p))) or any(
        d.is_dir() and any(next(d.glob(pat), None) for pat in ("*.exe", "*.dll", "UnityPlayer*")) for d in near)


def shot_dir() -> Path:
    with db.conn() as c:
        p = Path(db.profile(c)["kv"].get("screenshot_dir") or importer.DEFAULT_DIR)
    if game_folder(p):
        raise HTTPException(400, "Screenshot folder looks like a game/program folder. Pick a normal screenshot folder.")
    return p


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


SLIM = ("id", "name", "title", "name_jp", "title_jp", "type", "rarity", "release_jp", "release_gl", "char_id", "apt")


@app.get("/api/data")
def data():
    slim = lambda cards: [{k: c[k] for k in SLIM if k in c} | {"global": gamedata.on_global(c)} for c in cards.values()]
    cur = gamedata.curated()
    return {"meta": gamedata.meta(), "support": slim(gamedata.support_cards()), "uma": slim(gamedata.uma_cards()),
            "sparks": gamedata.spark_names(), "skills": gamedata.skill_names(), "tracks": gamedata.TRACKS,
            "curated": {k: cur.get(k) for k in ("retrieved", "global", "champions_meeting", "banners", "income", "sources")}}


@app.post("/api/data/update")
def data_update():
    p, w = state()
    sup = owned(p, "support")
    before = tools.snapshot(sup, w) if gamedata.support_cards() else {}
    counts = gamedata.update()
    importer.vocab.cache_clear()
    model._memo.clear()
    alerts = tools.diff_alerts(before, tools.snapshot(sup, w)) if before else []
    if alerts:
        with db.conn() as c:
            db.set_kv(c, "_alerts", json.dumps(db.get_json(c, "_alerts", []) + alerts))
    return counts | {"alerts": alerts}


def state() -> tuple[dict, dict]:
    """(profile, weights) for the advisors."""
    with db.conn() as c:
        p = db.profile(c)
    try:
        w = model.weights(p["kv"].get("weights"))
    except (ValueError, TypeError):
        w = model.weights()
    return p, w


def owned(p: dict, kind: str) -> dict[int, int]:
    return {o["id"]: o["level"] for o in p["owned"] if o["kind"] == kind}


@app.get("/api/weights")
def get_weights():
    p, w = state()
    return {"defaults": model.WEIGHTS | {"advisor": advisor.ADV}, "overrides": p["kv"].get("weights") or "{}"}


@app.get("/api/tiers/support")
def tiers_support():
    _, w = state()
    cur = {t["support_id"]: t for t in gamedata.curated().get("support_tiers", [])}
    return model.support_tiers(w, cur)


@app.get("/api/tiers/uma")
def tiers_uma():
    _, w = state()
    cur = {t["card_id"]: t for t in gamedata.curated().get("uma_tiers", [])}
    return model.uma_tiers(w, cur)


@app.get("/api/tiers/skill")
def tiers_skill():
    names = gamedata.skill_names()
    return [t | {"name": names.get(t["skill_id"], t["skill_id"])} for t in gamedata.curated().get("skill_tiers", [])]


def target_of(t: dict) -> dict:
    """Race target from the UI: distance_cat, distance_m, style, surface, track, direction (all optional but dist)."""
    dist = t.get("distance_cat") or "medium"
    if dist not in model.DISTANCES:
        raise HTTPException(400, "distance_cat must be short/mile/medium/long")
    out = {"distance_cat": dist, "distance_m": int(t.get("distance_m") or {"short": 1200, "mile": 1600, "medium": 2200, "long": 2500}[dist])}
    out |= {k: t[k] for k in ("style", "surface", "direction") if t.get(k)}
    if t.get("track"):
        out["track"] = int(t["track"])
    return out


@app.post("/api/deck")
def deck(req: dict = Body()):
    p, w = state()
    target = target_of(req)
    uma = gamedata.uma_cards().get(int(req.get("uma_id") or 0))
    d = model.build_deck(owned(p, "support"), target, uma and uma["char_id"], w,
                         borrow=bool(req.get("borrow", True)), forced=[int(x) for x in req.get("forced", [])])
    if uma:
        target.setdefault("style", model.uma_value(uma, target["distance_cat"], w)["style"])
        d["stats"] = {s: round(d["stats"][s] * (1 + uma["growth"][s] / 100)) for s in model.STATS}  # growth bonus
        d["parents"] = model.best_parents(p["veterans"], uma["id"], target, w)
    d["target"] = target
    return d


@app.post("/api/turn")
def turn(req: dict = Body()):
    _, w = state()
    return advisor.score_turn(req, w)


@app.get("/api/events")
def events(q: str, distance_cat: str = "medium"):
    _, w = state()
    return advisor.recommend_event(q, {}, distance_cat if distance_cat in model.DISTANCES else "medium", w)


@app.get("/api/races/{uma_id}")
def races(uma_id: int, min_grade: int = 300, min_apt: str = "B"):
    if min_apt not in model.APT_ORDER:
        raise HTTPException(400, "bad aptitude")
    return advisor.race_plan(uma_id, min_grade, min_apt)


@app.post("/api/skills")
def skills(req: dict = Body()):
    _, w = state()
    sp = int(req.get("sp") or 0)
    if not 0 <= sp <= 5000:
        raise HTTPException(400, "sp must be 0-5000")
    return advisor.optimize_skills(sp, req.get("skills", []), target_of(req), w,
                                   {int(x) for x in req.get("owned", [])})


@app.get("/api/banners")
def banner_info():
    return banners.forecast() | {"announced": gamedata.curated().get("banners", []),
                                 "cm": gamedata.curated().get("champions_meeting", [])}


@app.get("/api/income")
def income(until: str):
    p, _ = state()
    try:
        return banners.income(p["kv"], banners._d(until))
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")


@app.get("/api/advise/{kind}/{cid}")
def advise(kind: str, cid: int, rate: float = pulls.RATE_UP):
    p, w = state()
    if kind == "support":
        return banners.advise(cid, owned(p, "support"), p["kv"], w, rate)
    if kind == "uma":
        return banners.advise_uma(cid, set(owned(p, "uma")), p["kv"], w, rate)
    raise HTTPException(400, "kind must be support or uma")


@app.get("/api/pulls")
def pull_odds(n: int = 100, rate: float = pulls.RATE_UP, owned: int = 0, points: int = 0, spark_at: int = pulls.SPARK_AT):
    if not (0 <= n <= 2000 and 0 < rate < 1 and 0 <= owned <= 5 and 0 <= points and 0 <= spark_at):
        raise HTTPException(400, "out of range")
    return pulls.summary(n, rate, owned, points, spark_at)


@app.get("/api/profile")
def profile():
    with db.conn() as c:
        return db.profile(c)


@app.put("/api/owned")
def put_owned(kind: str = Body(), id: int = Body(), level: int = Body()):
    if kind not in ("support", "uma"):
        raise HTTPException(400, "kind must be support or uma")
    with db.conn() as c:
        try:
            db.set_owned(c, kind, id, level)
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.delete("/api/owned/{kind}/{cid}")
def delete_owned(kind: str, cid: int):
    with db.conn() as c:
        db.del_owned(c, kind, cid)


@app.put("/api/kv")
def put_kv(key: str = Body(), value: str = Body()):
    if key == "weights":
        try:
            model.weights(value)
        except (ValueError, TypeError, AttributeError) as e:
            raise HTTPException(400, f"weights must be a JSON object: {e}")
    if key == "screenshot_dir" and value and game_folder(Path(value)):
        raise HTTPException(400, "That looks like the game's install/save folder. Use a normal screenshot folder.")
    with db.conn() as c:
        db.set_kv(c, key, value)


@app.post("/api/veterans")
def put_veteran(v: dict = Body()):
    with db.conn() as c:
        try:
            return {"id": db.save_veteran(c, v)}
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e))


@app.delete("/api/veterans/{vid}")
def delete_veteran(vid: int):
    with db.conn() as c:
        db.del_veteran(c, vid)


@app.get("/api/import")
def import_list():
    folder = shot_dir()
    if not folder.is_dir():
        return {"folder": str(folder), "new": [], "error": "folder not found"}
    with db.conn() as c:
        done = db.imported(c)
    new = [p.name for p in importer.list_images(folder) if done.get(p.name) != p.stat().st_mtime]
    return {"folder": str(folder), "new": new}


def _shot(name: str) -> Path:
    folder = shot_dir().resolve()
    p = (folder / name).resolve()
    if p.parent != folder or p.suffix.lower() not in importer.IMAGE_EXT or not p.is_file():
        raise HTTPException(404)
    return p


@app.get("/api/import/{name}/image")
def import_image(name: str):
    return FileResponse(_shot(name))


@app.post("/api/import/{name}")
def import_read(name: str):
    return importer.read(_shot(name))


@app.post("/api/import/{name}/done")
def import_done(name: str):
    p = _shot(name)
    with db.conn() as c:
        db.mark_imported(c, p.name, p.stat().st_mtime)


# ---------- batch B: dashboard, veterans, QoL ----------

def kvjson(key: str, default):
    with db.conn() as c:
        return db.get_json(c, key, default)


def put_kvjson(key: str, value):
    with db.conn() as c:
        db.set_kv(c, key, json.dumps(value))


@app.get("/api/dashboard")
def dashboard():
    p, w = state()
    return tools.dashboard(p, w) | {"alerts": kvjson("_alerts", [])}


@app.delete("/api/alerts")
def clear_alerts():
    put_kvjson("_alerts", [])


@app.get("/api/veterans/cleanup")
def vet_cleanup(free: int = 0):
    if not 0 <= free <= 1000:
        raise HTTPException(400, "free must be 0-1000")
    p, w = state()
    return veterans.cleanup(p["veterans"], w, kvjson("planned_parents", []), kvjson("next_trainees", []), free)


@app.post("/api/veterans/{vid}/flag")
def vet_flag(vid: int, flag: str = Body(), on: bool = Body()):
    with db.conn() as c:
        try:
            db.set_vet_flag(c, vid, flag, on)
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.post("/api/parents/plan")
def parents_plan(trainee_id: int = Body(), wants: list = Body()):
    p, w = state()
    return veterans.plan_parents(p["veterans"], trainee_id, wants, w)


@app.get("/api/tt")
def tt():
    p, _ = state()
    return veterans.tt_lineup(p["veterans"])


@app.post("/api/tt/apply")
def tt_apply(ids: list[int] = Body(embed=True)):
    """Marks these veterans as 'in lineup' in this app (protects them in cleanup). Only adds flags, so
    hand-marked CM lineups stay protected; untick old ones yourself. Doesn't touch the game."""
    p, _ = state()
    with db.conn() as c:
        for v in p["veterans"]:
            if v["id"] in ids and not v["lineup"]:
                db.set_vet_flag(c, v["id"], "lineup", True)


@app.get("/api/checklist")
def get_checklist():
    p, _ = state()
    return {"items": tools.checklist(kvjson("checklist", []), p["kv"]), "resets": tools.resets(p["kv"])}


@app.post("/api/checklist/{i}/toggle")
def toggle_checklist(i: int):
    p, _ = state()
    items = kvjson("checklist", [])
    if not 0 <= i < len(items):
        raise HTTPException(404)
    done = tools.checklist(items, p["kv"])[i]["done"]
    items[i]["done_at"] = None if done else datetime.now(timezone.utc).isoformat()
    put_kvjson("checklist", items)


@app.get("/api/lbplan")
def lbplan():
    p, w = state()
    return tools.lb_planner(owned(p, "support"), w)


@app.get("/api/gaps")
def gap_report():
    p, w = state()
    return tools.gaps(owned(p, "support"), w)


@app.get("/api/whatif/{kind}/{cid}")
def whatif(kind: str, cid: int, lb: int = 0):
    if kind not in ("support", "uma") or not 0 <= lb <= 4:
        raise HTTPException(400, "bad kind or lb")
    p, w = state()
    return tools.whatif(kind, cid, lb, p, w)


@app.get("/api/log/{table}")
def log_rows(table: str):
    if table not in ("run", "pull_log", "history"):
        raise HTTPException(404)
    with db.conn() as c:
        return db.rows(c, table)[:500]


@app.post("/api/log/{table}")
def log_add(table: str, row: dict = Body()):
    if table not in ("run", "pull_log"):
        raise HTTPException(404)
    with db.conn() as c:
        return {"id": db.add_row(c, table, row)}


@app.delete("/api/log/{table}/{rid}")
def log_del(table: str, rid: int):
    if table not in ("run", "pull_log"):
        raise HTTPException(404)
    with db.conn() as c:
        db.del_row(c, table, rid)


@app.get("/api/luck")
def luck():
    with db.conn() as c:
        return tools.pull_luck(db.rows(c, "pull_log"))


@app.get("/api/runs/tuning")
def runs_tuning():
    _, w = state()
    with db.conn() as c:
        return tools.run_tuning(db.rows(c, "run"), w)


@app.post("/api/sim")
def sim(req: dict = Body()):
    _, w = state()
    dist = int(req.get("distance") or 2000)
    style = req.get("style") or "pace"
    if not 1000 <= dist <= 4000 or style not in racesim.STYLE_SPEED:
        raise HTTPException(400, "distance 1000-4000, style front/pace/late/end")
    apt = {k: req.get(k) or "A" for k in ("dist_apt", "surf_apt")}
    if any(v not in racesim.DIST_APT for v in apt.values()):
        raise HTTPException(400, "aptitude must be S-G")
    mine = {s: float(req.get("stats", {}).get(s) or 0) for s in model.STATS}
    field = req.get("field") or w["targets"][advisor.dist_cat(dist)]
    return racesim.compare(mine, field, dist, style, float(req.get("skill_lengths") or 0),
                           mood=min(4, max(0, int(req.get("mood", 4)))), heal_pct=float(req.get("heal_pct") or 0), **apt)


@app.get("/api/targets")
def targets(distance: int = 2000, style: str = "pace", heal: float = 0):
    if not 1000 <= distance <= 4000 or style not in racesim.STYLE_SPEED:
        raise HTTPException(400, "bad distance/style")
    _, w = state()
    return racesim.targets(distance, style, w["targets"][advisor.dist_cat(distance)], heal)


@app.get("/api/cm/{i}")
def cm(i: int):
    cups = gamedata.curated().get("champions_meeting", [])
    if not 0 <= i < len(cups):
        raise HTTPException(404)
    p, w = state()
    return tools.cm_prep(cups[i], p, w)


@app.get("/api/search")
def search(q: str):
    return tools.search(q)


@app.post("/api/import/{name}/events")
def import_events(name: str):
    """OCR a screenshot and look up any training event title in it."""
    _, w = state()
    seen, out = set(), []
    for line in importer.read(_shot(name))["lines"]:
        if len(line) < 6:
            continue
        for e in advisor.recommend_event(line, {}, "medium", w)[:1]:
            if e["name"] not in seen:
                seen.add(e["name"])
                out.append(e | {"line": line})
    return out


@app.get("/api/export")
def export():
    with db.conn() as c:
        data = db.export(c)
    name = f"trainer-notebook-{datetime.now():%Y%m%d}.json"
    return JSONResponse(data, headers={"Content-Disposition": f"attachment; filename={name}"})


@app.post("/api/restore")
def restore(data: dict = Body()):
    with db.conn() as c:
        try:
            db.restore(c, data)
        except Exception as e:  # anything malformed: the transaction rolled back, nothing changed
            raise HTTPException(400, f"restore failed, nothing changed: {e}")


if __name__ == "__main__":
    if found := safety_check.scan_repo():
        print("Refusing to start, safety check failed:", *found, sep="\n  ")
        sys.exit(1)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)
