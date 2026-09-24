"""Run advisors: training turn helper, event choices, race schedule, skill point optimizer.

Safety: advice from numbers the user types in (read off their own screen by eye) plus the cached public DB.
It never reads the game, never presses anything: the user makes every move (rule 3).

APPROXIMATIONS: the turn scorer is a weighted heuristic with a one-step energy look-ahead, not the game's
formula or a full simulation. Constants are in model.WEIGHTS / ADV and user-editable.
"""
import re
from difflib import SequenceMatcher

import gamedata as gd
import model

STATS = gd.STATS
MOODS = ["awful", "bad", "normal", "good", "great"]
ADV = {  # approx, editable via weights override key "advisor"
    "stat_cap": 1200, "career_turns": 72,
    "bond_value": 6,          # stat-points per bond gain early in the run (fades by turn 48)
    "hint_value": 12, "rainbow_value": 4,
    "fail_cost": 60,          # stat-points lost on a failed training (stats + mood)
    "energy_per_train": 20, "rest_energy": 50, "energy_low": 50,
    "mood_value": 15,         # per mood step below Great
    "race_value": 45,         # typical optional race: stats + skill points + fans
    "race_energy": 15,
}


def _adv(w: dict) -> dict:
    return ADV | w.get("advisor", {})


def stat_weights(stats: dict, dist: str, w: dict) -> dict:
    """Marginal value of +1 in each stat: distance priority x how far from target, 0 past the cap."""
    a = _adv(w)
    out = {}
    for s in STATS:
        target = w["targets"][dist][s]
        need = max(0.1, min(1.0, (target - stats.get(s, 0)) / max(target, 1) * 2))
        out[s] = 0 if stats.get(s, 0) >= a["stat_cap"] else w["priority"][dist][s] * need
    return out


def score_turn(state: dict, w: dict) -> list[dict]:
    """Rank this turn's actions. state: turn, stats{}, energy, mood(0-4), distance_cat, race_available,
    options: [{name, gains{stat: n, sp: n}, fail, bonds, rainbows, hint}]"""
    a = _adv(w)
    turn, energy, mood = state.get("turn", 1), state.get("energy", 100), state.get("mood", 4)
    sw = stat_weights(state.get("stats", {}), state.get("distance_cat", "medium"), w)
    early = max(0.0, 1 - turn / 48)
    # energy look-ahead: training while low means next turns fail more / need a rest
    low_penalty = lambda e: max(0, a["energy_low"] - e) * 0.8
    out = []
    for o in state.get("options", []):
        g = o.get("gains", {})
        stat_pts = sum(sw[s] * g.get(s, 0) for s in STATS)
        cost = a["energy_per_train"] if o.get("name") != "wit" else -5
        parts = {
            "stats": stat_pts,
            "skill pts": g.get("sp", 0) * w["sp_to_stat"],
            "bonds": o.get("bonds", 0) * a["bond_value"] * early,
            "rainbows": o.get("rainbows", 0) * a["rainbow_value"],
            "hint": a["hint_value"] if o.get("hint") else 0,
            "failure risk": -o.get("fail", 0) / 100 * (stat_pts + a["fail_cost"]),
            "energy after": -low_penalty(energy - cost),
        }
        out.append({"action": f"Train {o.get('name', '?')}", "score": round(sum(parts.values()), 1),
                    "parts": {k: round(v, 1) for k, v in parts.items() if v}})
    best_train = max((x["score"] for x in out), default=0)
    rest_gain = min(100, energy + a["rest_energy"]) - energy
    out.append({"action": "Rest", "score": round(low_penalty(energy) * 2.5 + rest_gain * 0.1, 1),
                "parts": {"energy": rest_gain}})
    mood_gap = 4 - mood
    out.append({"action": "Recreation (mood)", "score": round(mood_gap * a["mood_value"] + (10 if energy < 40 else 0), 1),
                "parts": {"mood steps": mood_gap}})
    if state.get("race_available"):
        out.append({"action": "Race", "score": round(a["race_value"] - low_penalty(energy - a["race_energy"]), 1),
                    "parts": {"race": a["race_value"]}})
    out.sort(key=lambda x: -x["score"])
    for x in out:
        x["why"] = ", ".join(f"{k} {v:+g}" for k, v in x["parts"].items())
    if out and out[0]["action"].startswith("Train") and best_train < 10:
        out[0]["why"] += " (all options weak: consider resting or racing)"
    return out


# ---------- events ----------

EFFECT_RE = re.compile(r"(Speed|Stamina|Power|Guts|Wit|Wisdom|Intelligence|Skill Pts|Skill points|Energy|Mood|Bond|All stats|Maximum Energy)\s*([+-]\d+)", re.I)
HINT_RE = re.compile(r"hint\s*\+\s*(\d+)", re.I)


def option_value(text: str, stats: dict, dist: str, w: dict) -> float:
    a = _adv(w)
    sw = stat_weights(stats, dist, w)
    v = 0.0
    for name, num in EFFECT_RE.findall(text):
        n, k = int(num), name.lower()
        if k in ("wisdom", "intelligence"):
            k = "wit"
        if k in sw:
            v += sw[k] * n
        elif k == "all stats":
            v += sum(sw.values()) * n
        elif k.startswith("skill"):
            v += n * w["sp_to_stat"]
        elif k == "energy":
            v += n * w["energy_value"]
        elif k == "mood":
            v += n * a["mood_value"]
        elif k == "bond":
            v += n / 5 * a["bond_value"]
        elif k == "maximum energy":
            v += n * 2
    v += sum(int(h) for h in HINT_RE.findall(text)) * a["hint_value"]
    return round(v, 1)


def find_events(query: str, limit: int = 8) -> list[dict]:
    q = query.lower().strip()
    if not q:
        return []
    scored = []
    for e in gd.events():
        n = e["name"].lower()
        s = 1.0 if q in n else SequenceMatcher(None, q, n).ratio()
        if s >= 0.6:
            scored.append((s, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:limit]]


def recommend_event(query: str, stats: dict, dist: str, w: dict) -> list[dict]:
    out = []
    for e in find_events(query):
        opts = [{"option": k or "(only option)", "effects": v, "value": option_value(v, stats, dist, w),
                 "random": bool(re.search(r"random|chance|or\b|may", v, re.I))} for k, v in e["options"].items()]
        best = max(opts, key=lambda o: o["value"], default=None)
        for o in opts:
            o["best"] = o is best and len(opts) > 1
        out.append({"name": e["name"], "source": e["source"], "options": opts})
    return out


# ---------- race schedule ----------

GRADES = {100: "G1", 200: "G2", 300: "G3", 400: "OP", 700: "Pre-OP"}
YEARS = {1: "Junior", 2: "Classic", 3: "Senior", 4: "Finals"}


def dist_cat(m: int) -> str:
    return "short" if m <= 1400 else "mile" if m <= 1800 else "medium" if m <= 2400 else "long"


def race_plan(uma_id: int, min_grade: int = 300, min_apt: str = "B") -> dict:
    """Races the trainee can run well (surface + distance aptitude >= min_apt), plus career objectives."""
    u = gd.uma_cards().get(uma_id)
    if not u:
        return {"races": [], "objectives": []}
    ok = lambda letter: model.APT_ORDER.index(letter) >= model.APT_ORDER.index(min_apt)
    races = []
    for r in gd._raw("race_instances"):
        d = r["details"]
        if d.get("grade", 999) > min_grade or d.get("distance", 99999) > 5000:
            continue
        cat, surface = dist_cat(d["distance"]), "turf" if d.get("terrain") == 1 else "dirt"
        if not (ok(u["apt"][cat]) and ok(u["apt"][surface])):
            continue
        turn = (r["year"] - 1) * 24 + (r["month"] - 1) * 2 + r["half"]
        races.append({"turn": turn, "when": f"{YEARS.get(r['year'], r['year'])} {r['month']}/{'early' if r['half'] == 1 else 'late'}",
                      "name": d["name_en"], "grade": GRADES.get(d["grade"], d["grade"]), "distance": d["distance"],
                      "surface": surface, "track": gd.TRACKS.get(d.get("track"), ""), "fans": r.get("fans_gain"),
                      "apt": u["apt"][cat] + u["apt"][surface]})
    races.sort(key=lambda r: (r["turn"], r["grade"]))
    seen, uniq = set(), []
    for r in races:  # race_instances repeats per scenario
        if (r["turn"], r["name"]) not in seen:
            seen.add((r["turn"], r["name"]))
            uniq.append(r)
    return {"races": uniq, "objectives": gd.objectives().get(uma_id, [])}


# ---------- skill point optimizer ----------

def optimize_skills(sp: int, skills: list[dict], target: dict, w: dict, owned: set[int] = frozenset()) -> dict:
    """Multiple-choice knapsack: max total lengths within sp. skills: [{id, hint}].
    An upgrade (◎ / gold, id ending in 1) needs its base (id + 1): each base+upgrade family is one group
    with options none / base / base+upgrade (or just upgrade if the base is owned)."""
    by_id = {int(s["id"]): int(s.get("hint", 0)) for s in skills}
    groups: dict[int, list] = {}
    for sid in by_id:
        base = sid + 1 if sid % 10 == 1 and (sid + 1) in gd.skills() else sid
        groups.setdefault(base, [])
    for base in groups:
        opts = []
        up = base - 1 if (base - 1) in by_id and (base - 1) % 10 == 1 else None
        base_cost = 0 if base in owned else model.skill_cost(base, by_id.get(base, 0), {base})
        base_val = 0 if base in owned else model.skill_value(base, target, w)
        if base in by_id and base not in owned:
            opts.append((base_cost, base_val, [base]))
        if up:
            c = model.skill_cost(up, by_id[up], {base}) + base_cost
            v = model.skill_value(up, target, w) + base_val
            opts.append((c, v, ([] if base in owned else [base]) + [up]))
        groups[base] = [o for o in opts if o[0] > 0]
    # DP over sp: best[c] = (value, chosen ids)
    best = [(0.0, [])] * (sp + 1)
    for opts in groups.values():
        new = best[:]
        for cost, val, ids in opts:
            for c in range(cost, sp + 1):
                cand = best[c - cost][0] + val
                if cand > new[c][0]:
                    new[c] = (cand, best[c - cost][1] + ids)
        best = new
    value, chosen = max(best, key=lambda x: x[0])
    names = gd.skill_names()
    rows = [{"id": i, "name": names.get(i, i), "cost": model.skill_cost(i, by_id.get(i, 0), set(chosen) | owned),
             "lengths": model.skill_value(i, target, w)} for i in chosen]
    return {"chosen": rows, "lengths": round(value, 2), "spent": sum(r["cost"] for r in rows), "budget": sp}


def _selftest():
    w = model.weights()
    st = {"turn": 20, "stats": {"speed": 300, "stamina": 200, "power": 200, "guts": 150, "wit": 150}, "energy": 80,
          "mood": 4, "distance_cat": "medium",
          "options": [{"name": "speed", "gains": {"speed": 20, "power": 8}, "fail": 2, "bonds": 2},
                      {"name": "guts", "gains": {"guts": 8}, "fail": 0}]}
    r = score_turn(st, w)
    assert r[0]["action"] == "Train speed", r
    st["energy"], st["options"][0]["fail"] = 10, 45
    assert score_turn(st, w)[0]["action"] == "Rest", score_turn(st, w)[:2]
    assert option_value("Speed +10\nEnergy -20", st["stats"], "medium", w) < option_value("Speed +10", st["stats"], "medium", w)
    if gd.skills():
        target = {"distance_cat": "medium", "distance_m": 2200, "style": "pace", "surface": "turf"}
        res = optimize_skills(400, [{"id": 200331, "hint": 0}, {"id": 200332, "hint": 0}], target, w)
        ids = [c["id"] for c in res["chosen"]]
        assert 200331 not in ids or 200332 in ids, ids  # never the gold without its base
        assert res["spent"] <= 400
    print("advisor selftest: PASS")


if __name__ == "__main__":
    _selftest()
