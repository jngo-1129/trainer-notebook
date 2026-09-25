"""Scoring model: support card value, uma value, skill value, deck builder, parent pairs.

Safety: pure computation on the cached public game DB and the local profile. No network, no game access.

APPROXIMATIONS: the game's real training formulas aren't public. Everything here is a community-style
approximation (marked "approx" below). Every constant lives in WEIGHTS, and the user can override any of them
from the Settings tab (stored as kv 'weights'). Treat scores as a ranking aid, not a prediction.
"""
import json
import math
import re
from functools import lru_cache

import gamedata as gd

STATS = gd.STATS
DISTANCES = ["short", "mile", "medium", "long"]
STYLES = ["front", "pace", "late", "end"]

WEIGHTS = {
    # approx: how much each stat matters, by race distance (1 = most)
    "priority": {
        "short": {"speed": 1.0, "stamina": 0.3, "power": 0.9, "guts": 0.4, "wit": 0.7},
        "mile": {"speed": 1.0, "stamina": 0.5, "power": 0.9, "guts": 0.4, "wit": 0.7},
        "medium": {"speed": 1.0, "stamina": 0.8, "power": 0.8, "guts": 0.45, "wit": 0.65},
        "long": {"speed": 1.0, "stamina": 1.0, "power": 0.75, "guts": 0.5, "wit": 0.6},
    },
    # approx: stat targets for a solid Champions Meeting build (Global stat cap 1200)
    "targets": {
        "short": {"speed": 1200, "stamina": 450, "power": 1000, "guts": 400, "wit": 800},
        "mile": {"speed": 1200, "stamina": 600, "power": 1000, "guts": 400, "wit": 800},
        "medium": {"speed": 1200, "stamina": 800, "power": 900, "guts": 450, "wit": 700},
        "long": {"speed": 1150, "stamina": 1000, "power": 900, "guts": 500, "wit": 600},
    },
    # approx: training model
    "train_turns": 60,          # training turns in a career
    "gain_main": 30,            # main stat of a typical mid-run training with other supports on it
    "gain_sub": 12,             # secondary stat of that training
    "per_support": 0.05,        # community formula: +5% per support card on the training
    "mood_bonus": 0.2,          # Great mood multiplier
    "bond_per_train": 7,        # bond gauge per training together
    "pick_early": 0.5,          # share of appearances you train with a card while bonding
    "pick_rainbow": 0.8,        # share of specialty appearances you take once rainbow
    "pick_normal": 0.25,        # share of other appearances you train with it
    "races": 10, "race_gain": 10,
    "hint_rate": 0.06, "hint_sp": 25, "hint_level_sp": 10,
    "sp_to_stat": 0.5,          # 1 skill point is worth this many stat points
    "energy_value": 0.6, "fail_value": 0.8,  # stat points per energy / per % failure avoided
    "event_energy": 100, "event_stats": 150,  # energy and stats a card's events give per run
    # approx: skill value in "lengths"; stat points per length
    "pts_per_length": 40,
    "accel_k": 3.0, "random_cond_factor": 0.7, "debuff_factor": 0.6, "debuff_cap": 1.5,  # debuffs hit rivals unevenly
    "heal_lengths": {"short": 8, "mile": 12, "medium": 18, "long": 24},  # lengths per 100% HP healed
    "passive_lengths_per_pt": 0.01,
    "hint_realize": 0.4,        # chance a deck hint skill actually shows up in a run
    # parents
    "blue": 1.0, "pink_fix": 3.0, "green": 1.5, "white": 0.6, "aff_weight": 0.05, "aff_scale": 200,
    "tier_pct": {"S": 90, "A": 70, "B": 40},
    "pull_threshold": 15,       # min expected deck-score gain to recommend pulling
    "advisor": {},              # overrides for advisor.ADV (turn helper / events)
    "veterans": {},             # overrides for veterans.VET (cleanup scoring)
}

LEVELS = [1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50]
MAX_LEVEL = {3: [30, 35, 40, 45, 50], 2: [25, 30, 35, 40, 45], 1: [20, 25, 30, 35, 40]}
SUB_STAT = {"speed": "power", "stamina": "guts", "power": "stamina", "guts": "power", "wit": "speed"}
APT_MULT = {"S": 1.05, "A": 1.0, "B": 0.85, "C": 0.65, "D": 0.4, "E": 0.3, "F": 0.2, "G": 0.1}
APT_ORDER = "GFEDCBAS"


# Multipliers fitted by `python evaluate.py` against Game8's full support tier list (158 cards): they raised
# held-out within-type ranking agreement from 62.1% to 72.3% (20/20 splits). See docs/EVALUATION.md. Single values
# aren't meaningful on their own (many settings rank equally well); only the combination was validated.
CALIBRATED = {"gain_main": 1.15, "gain_sub": 1.66, "per_support": 1.23, "mood_bonus": 0.66, "bond_per_train": 1.38,
              "pick_early": 1.9, "pick_rainbow": 1.15, "pick_normal": 0.56, "races": 1.54, "race_gain": 1.68,
              "hint_rate": 0.8, "hint_sp": 0.6, "hint_level_sp": 0.65, "sp_to_stat": 1.05, "energy_value": 1.68,
              "fail_value": 1.21, "event_energy": 1.8, "event_stats": 0.67}
PROBABILITIES = {"pick_early", "pick_rainbow", "pick_normal", "hint_rate"}


def weights(overrides: str | dict | None = None, calibrated: bool = True) -> dict:
    """WEIGHTS (x CALIBRATED unless calibrated=False), then user overrides merged (one level deep for dicts)."""
    o = json.loads(overrides) if isinstance(overrides, str) and overrides else overrides or {}
    w = json.loads(json.dumps(WEIGHTS))
    if calibrated:
        for k, f in CALIBRATED.items():
            w[k] = min(w[k] * f, 0.95) if k in PROBABILITIES else w[k] * f
    for k, v in o.items():
        if isinstance(v, dict) and isinstance(w.get(k), dict):
            for k2, v2 in v.items():
                w[k][k2] = w[k][k2] | v2 if isinstance(v2, dict) and isinstance(w[k].get(k2), dict) else v2
        elif k in w:
            w[k] = v
    w["_key"] = hash(json.dumps(w, sort_keys=True))  # memo key for this weight set
    return w


_memo: dict = {}


def _memoized(fn):
    """Cache per (weights, args): scores are recomputed thousands of times by the deck search."""
    def wrapper(obj, arg, ctx, w):
        key = (fn.__name__, w.get("_key"), obj["id"] if isinstance(obj, dict) else obj, arg,
               tuple(sorted(ctx.items())) if isinstance(ctx, dict) else ctx)
        if key not in _memo:
            if len(_memo) > 200_000:
                _memo.clear()
            _memo[key] = fn(obj, arg, ctx, w)
        return _memo[key]
    return wrapper


# ---------- support cards ----------

def effects_at(card: dict, lb: int) -> tuple[dict[int, float], list[str]]:
    """Effect id -> value at the max level for this limit break (linear between GameTora breakpoints)."""
    level = MAX_LEVEL.get(card["rarity"], MAX_LEVEL[3])[lb]
    out, notes = {}, []
    for row in card["effects"]:
        pts = [(LEVELS[i], v) for i, v in enumerate(row[1:]) if v != -1]
        below = [p for p in pts if p[0] <= level]
        if not below:
            continue
        (l0, v0), above = below[-1], [p for p in pts if p[0] > level]
        v = v0 + (above[0][1] - v0) * (level - l0) / (above[0][0] - l0) if above else v0
        out[row[0]] = math.floor(v)
    u = card.get("unique") or {}
    if u and level >= u.get("level", 99):
        for e in u.get("effects", []):
            if e["type"] <= 31:
                out[e["type"]] = out.get(e["type"], 0) + e["value"]
            else:
                notes.append("has a conditional unique effect (not modeled)")
    return out, notes


@_memoized
def card_value(card: dict, lb: int, priority: dict, w: dict) -> dict:
    """approx: expected stat-equivalent points a card adds over one career."""
    e, notes = effects_at(card, lb)
    g = lambda k: e.get(k, 0)
    main = card["type"].lower()
    special = main not in STATS  # Friend / Group cards: no specialty training, no rainbow
    if special:
        main = "speed"
        notes.append(f"{card['type']} card: scenario-specific mechanics not modeled, check the community tier")
    sub = SUB_STAT[main]
    sp_pri = g(19)
    p_spec = (100 + sp_pri) / (550 + sp_pri)
    p_any = 500 / (550 + sp_pri)
    bond_needed = max(0, math.ceil((80 - g(14)) / w["bond_per_train"]))
    bond_turns = min(w["train_turns"], bond_needed / max(0.05, p_any * w["pick_early"]))
    rainbow = 0 if special else (w["train_turns"] - bond_turns) * p_spec * w["pick_rainbow"]
    together = w["train_turns"] * p_any * w["pick_normal"]
    mood = (1 + w["mood_bonus"] * (1 + g(2) / 100)) / (1 + w["mood_bonus"])
    m_tog = (1 + g(8) / 100) * mood * (1 + w["per_support"])
    m_rb = m_tog * (1 + g(1) / 100)
    sb = {s: g(3 + i) for i, s in enumerate(STATS)}
    pts = dict.fromkeys(STATS, 0.0)
    parts = {}
    for stat, base in ((main, w["gain_main"]), (sub, w["gain_sub"])):
        pts[stat] += rainbow * ((base + sb[stat]) * m_rb - base) + together * ((base + sb[stat]) * m_tog - base)
    parts["rainbow training"] = rainbow * ((w["gain_main"] + sb[main]) * m_rb - w["gain_main"]) * priority[main]
    for i, s in enumerate(STATS):
        pts[s] += g(9 + i)
    race = w["races"] * w["race_gain"] * g(15) / 100
    for s in STATS:
        pts[s] += race / 5
    sp = g(30) * (rainbow + together) + (rainbow + together) * w["hint_rate"] * (1 + g(18) / 100) * (
        w["hint_sp"] + g(17) * w["hint_level_sp"])
    energy = w["train_turns"] * 20 * g(28) / 100 + w["event_energy"] * g(25) / 100  # energy saved / recovered
    misc = (energy * w["energy_value"] + w["train_turns"] * g(27) / 100 * w["fail_value"] * 5
            + w["event_stats"] * g(26) / 100)
    total = sum(pts[s] * priority[s] for s in STATS) + sp * w["sp_to_stat"] + misc
    parts |= {"initial stats": sum(g(9 + i) * priority[s] for i, s in enumerate(STATS)),
              "skill points & hints": sp * w["sp_to_stat"], "race bonus": race * 0.7, "energy & events": misc}
    why = [f"{g(1)}% friendship", f"{g(8)}% training eff." if g(8) else "", f"specialty {g(19)}" if g(19) else "",
           f"initial bond {g(14)}" + ("" if special else f" (rainbow ~turn {bond_turns:.0f})"), f"hint lv {g(17)}" if g(17) else "",
           f"race bonus {g(15)}%" if g(15) else ""]
    return {"total": round(total, 1), "stats": {s: round(v) for s, v in pts.items()}, "sp": round(sp),
            "rainbow_turns": round(rainbow, 1), "parts": {k: round(v) for k, v in parts.items()},
            "why": ", ".join(x for x in why if x), "notes": notes}


def generic_priority(w: dict) -> dict:
    return {s: sum(w["priority"][d][s] for d in DISTANCES) / 4 for s in STATS}


def _tiers(scores: list[float], w: dict):
    s = sorted(scores)
    cut = {t: s[min(len(s) - 1, int(len(s) * p / 100))] for t, p in w["tier_pct"].items()} if s else {}
    return lambda x: next((t for t in ("S", "A", "B") if x >= cut[t]), "C") if cut else "C"


def support_tiers(w: dict, curated: dict | None = None) -> list[dict]:
    """Every Global support card at each LB, with a model tier (cut-offs from MLB scores) and the community tier."""
    pri = generic_priority(w)
    curated = curated or {}
    rows = []
    for c in gd.support_cards().values():
        if not gd.on_global(c):
            continue
        vals = [card_value(c, lb, pri, w) for lb in range(5)]
        rows.append({"id": c["id"], "name": c["name"], "title": c["title"], "type": c["type"], "rarity": c["rarity"],
                     "scores": [v["total"] for v in vals], "why": vals[4]["why"], "notes": vals[4]["notes"],
                     "parts": vals[4]["parts"], "community": curated.get(c["id"])})
    stat_rows = [r for r in rows if r["type"] not in ("Friend", "Group")]
    tier = _tiers([r["scores"][4] for r in stat_rows], w)
    for r in rows:  # Friend/Group cards aren't comparable with the stat-card model: community tier only
        r["tiers"] = [tier(x) for x in r["scores"]] if r in stat_rows else ["—"] * 5
    return sorted(rows, key=lambda r: (r["tiers"][4] == "—", -r["scores"][4]))


# ---------- skills ----------

COND_KEYS = {"distance_type": ("distance_cat", {1: "short", 2: "mile", 3: "medium", 4: "long"}),
             "running_style": ("style", {1: "front", 2: "pace", 3: "late", 4: "end"}),
             "ground_type": ("surface", {1: "turf", 2: "dirt"}),
             "rotation": ("direction", {1: "right", 2: "left"}),
             "track_id": ("track", None)}
CLAUSE = re.compile(r"(\w+)(==|!=)(\d+)")


def _cond_ok(cond: str, target: dict) -> bool:
    """True if any OR-branch (@) of a skill condition can hold for this race/style."""
    if not cond:
        return True
    for branch in cond.split("@"):
        ok = True
        for var, op, val in CLAUSE.findall(branch):
            if var not in COND_KEYS:
                continue
            key, mapping = COND_KEYS[var]
            want = target.get(key)
            if want is None:
                continue
            have = mapping.get(int(val)) if mapping else int(val)
            if (op == "==" and have != want) or (op == "!=" and have == want):
                ok = False
        if ok:
            return True
    return False


def skill_value(skill_id: int, target: dict, w: dict) -> float:
    """approx: lengths gained by a skill in this race (0 if its conditions can't trigger)."""
    return _skill_value(skill_id, None, target, w)


@_memoized
def _skill_value(skill_id: int, _unused, target: dict, w: dict) -> float:
    s = gd.skills().get(skill_id)
    if not s:
        return 0.0
    dist = target.get("distance_cat", "medium")
    scale = target.get("distance_m", 2000) / 1000
    total = 0.0
    is_debuff = "dbf" in (s.get("type") or [])
    for grp in s.get("condition_groups", []):
        if not _cond_ok(grp.get("condition", ""), target) or not _cond_ok(grp.get("precondition", ""), target):
            continue
        t = max(grp.get("base_time", 0), 0) / 10000 * scale
        f = w["random_cond_factor"] if "random" in grp.get("condition", "") else 1.0
        for e in grp.get("effects", []):
            v = e.get("value", 0) / 10000
            if v < 0 and not is_debuff:
                continue  # a penalty on yourself (event/joke skills), not a debuff on rivals
            debuff = w["debuff_factor"] if v < 0 else 1.0
            v = abs(v)
            if e["type"] in (27, 21, 22):  # target / current speed
                total += v * t / 2.5 * f * debuff
            elif e["type"] == 31:  # acceleration
                total += v * t * w["accel_k"] / 2.5 * f * debuff
            elif e["type"] == 9:  # recovery (or drain on others)
                total += v * w["heal_lengths"][dist] * f * debuff
            elif 1 <= e["type"] <= 5:  # passive stat boost
                total += v * w["passive_lengths_per_pt"] * f
    return round(min(total, w["debuff_cap"]) if is_debuff else total, 2)


def skill_cost(skill_id: int, hint_level: int = 0, owned: set[int] = frozenset()) -> int:
    """approx: SP cost after hint discount (community: 10/20/30/35/40% for hint lv 1-5).
    A gold skill also needs its white base (id + 1) unless already owned."""
    discount = [0, 0.1, 0.2, 0.3, 0.35, 0.4][max(0, min(5, hint_level))]
    s = gd.skills().get(skill_id) or {}
    cost = round((s.get("cost") or 0) * (1 - discount))
    base = skill_id + 1
    if s.get("rarity") == 2 and base in gd.skills() and base not in owned:
        cost += gd.skills()[base].get("cost") or 0
    return cost


# ---------- umas ----------

def uma_value(uma: dict, dist: str, w: dict) -> dict:
    pri = w["priority"][dist]
    apt = APT_MULT.get(uma["apt"][dist], 0.1) * max(APT_MULT.get(uma["apt"]["turf"], 0), APT_MULT.get(uma["apt"]["dirt"], 0))
    style = max(STYLES, key=lambda st: APT_ORDER.index(uma["apt"][st]))
    growth = sum(uma["growth"][s] / 100 * 600 * pri[s] for s in STATS)
    target = {"distance_cat": dist, "style": style, "distance_m": {"short": 1200, "mile": 1600, "medium": 2200, "long": 3000}[dist]}
    uniq = skill_value(uma["unique_skill"], target, w) if uma["unique_skill"] else 0
    score = apt * (1000 + growth + uniq * w["pts_per_length"] * 3)
    return {"score": round(score), "style": style, "apt": uma["apt"][dist], "growth": uma["growth"], "unique_lengths": uniq}


def uma_tiers(w: dict, curated: dict | None = None) -> list[dict]:
    curated = curated or {}
    rows = []
    for u in gd.uma_cards().values():
        if not gd.on_global(u):
            continue
        by = {d: uma_value(u, d, w) for d in DISTANCES}
        rows.append({"id": u["id"], "name": u["name"], "title": u["title"], "by": by, "community": curated.get(u["id"])})
    for d in DISTANCES:
        tier = _tiers([r["by"][d]["score"] for r in rows if r["by"][d]["apt"] in "SA"], w)
        for r in rows:
            r["by"][d]["tier"] = tier(r["by"][d]["score"]) if r["by"][d]["apt"] in "SA" else "-"
    return rows


# ---------- deck builder ----------

def deck_score(cards: list[tuple[dict, int]], target: dict, w: dict) -> dict:
    pri = w["priority"][target["distance_cat"]]
    vals = [card_value(c, lb, pri, w) for c, lb in cards]
    stats = {s: sum(v["stats"][s] for v in vals) for s in STATS}
    hints = {sid for c, _ in cards for sid in c["hint_skills"]}
    hint_vals = sorted(((skill_value(h, target, w), h) for h in hints), reverse=True)
    hint_pts = sum(v for v, _ in hint_vals) * w["pts_per_length"] * w["hint_realize"]
    return {"score": round(sum(v["total"] for v in vals) + hint_pts, 1), "stats": stats,
            "cards": [v["total"] for v in vals],
            "hints": [{"id": h, "name": gd.skill_names().get(h, h), "lengths": v} for v, h in hint_vals if v > 0][:12]}


def build_deck(owned: dict[int, int], target: dict, trainee_char: int | None, w: dict,
               borrow: bool = True, forced: list[int] = ()) -> dict:
    """Best 6-card deck from owned cards (id -> LB), optionally with one borrowed MLB card.
    ponytail: greedy + one swap pass, not exhaustive. Good enough for ranking; exact search if it disappoints."""
    cards = gd.support_cards()
    pri = w["priority"][target["distance_cat"]]
    pool = [(cards[i], lb, False) for i, lb in owned.items() if i in cards]
    if borrow:
        extra = sorted((c for c in cards.values() if gd.on_global(c) and c["rarity"] == 3 and c["id"] not in owned),
                       key=lambda c: -card_value(c, 4, pri, w)["total"])[:15]
        pool += [(c, 4, True) for c in extra]
    val = {(c["id"], b): card_value(c, lb, pri, w)["total"] for c, lb, b in pool}
    ok = lambda deck, cand: (cand[0]["char_id"] != trainee_char and all(cand[0]["char_id"] != d[0]["char_id"] for d in deck)
                             and not (cand[2] and any(d[2] for d in deck)))
    ranked = sorted(pool, key=lambda x: -val[(x[0]["id"], x[2])])
    deck = [x for x in pool if x[0]["id"] in forced and not x[2]]
    for cand in ranked:
        if len(deck) == 6:
            break
        if cand not in deck and ok(deck, cand):
            deck.append(cand)
    score = lambda d: deck_score([(c, lb) for c, lb, _ in d], target, w)["score"]
    best = score(deck)
    for i in range(len(deck)):  # one swap pass: lets hint skills and duplicates matter
        if deck[i][0]["id"] in forced:
            continue
        for cand in ranked[:40]:
            if cand in deck:
                continue
            trial = deck[:i] + deck[i + 1:]
            if ok(trial, cand) and (s := score(trial[:i] + [cand] + trial[i:])) > best:
                deck, best = trial[:i] + [cand] + trial[i:], s
    ds = deck_score([(c, lb) for c, lb, _ in deck], target, w)
    return ds | {"deck": [{"id": c["id"], "label": f"{c['title']} {c['name']}", "type": c["type"], "lb": lb,
                           "borrow": b, "value": v} for (c, lb, b), v in zip(deck, ds["cards"])]}


# ---------- parents ----------

PINK = {"Turf": "turf", "Dirt": "dirt", "Sprint": "short", "Short": "short", "Mile": "mile", "Medium": "medium",
        "Long": "long", "Front Runner": "front", "Pace Chaser": "pace", "Late Surger": "late", "End Closer": "end"}


@lru_cache(maxsize=4096)
def _skill_by_name(name: str) -> int | None:
    return next((i for i, n in gd.skill_names().items() if n == name), None)


def spark_value(spark: dict, trainee: dict, target: dict, w: dict) -> float:
    stars, name = spark["stars"], spark["name"]
    if spark["color"] == "blue":
        stat = {"Speed": "speed", "Stamina": "stamina", "Power": "power", "Guts": "guts", "Wit": "wit"}.get(name)
        return stars * 10 * w["blue"] * w["priority"][target["distance_cat"]].get(stat, 0.3)
    if spark["color"] == "pink":
        key = PINK.get(name)
        if not key:
            return stars
        gap = max(0, APT_ORDER.index("A") - APT_ORDER.index(trainee["apt"][key]))
        relevant = key in (target["distance_cat"], target.get("style"), target.get("surface", "turf"))
        return stars * (w["pink_fix"] * 10 * gap if relevant else 2)
    if spark["color"] in ("green", "white"):
        sid = _skill_by_name(name)
        v = skill_value(sid, target, w) if sid else 0.2
        return stars * v * w["pts_per_length"] / 10 * (w["green"] if spark["color"] == "green" else w["white"])
    return 0.0


def best_parents(veterans: list[dict], trainee_id: int, target: dict, w: dict, top: int = 5) -> list[dict]:
    """Rank veteran pairs as parents. Affinity ignores grandparents (not stored)."""
    umas = gd.uma_cards()
    t = umas[trainee_id]
    cands = [v for v in veterans if v["card_id"] in umas and umas[v["card_id"]]["char_id"] != t["char_id"]]
    val = {v["id"]: sum(spark_value(s, t, target, w) for s in v["sparks"]) for v in cands}
    pairs = []
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            ca, cb = umas[a["card_id"]]["char_id"], umas[b["card_id"]]["char_id"]
            if ca == cb:
                continue
            aff = gd.affinity(t["char_id"], ca) + gd.affinity(t["char_id"], cb) + gd.affinity(t["char_id"], ca, cb)
            score = (val[a["id"]] + val[b["id"]]) * (1 + aff / w["aff_scale"]) + aff * w["aff_weight"]
            pairs.append({"ids": [a["id"], b["id"]], "score": round(score, 1), "affinity": aff,
                          "labels": [gd.label("uma", a["card_id"]), gd.label("uma", b["card_id"])],
                          "values": [round(val[a["id"]], 1), round(val[b["id"]], 1)]})
    return sorted(pairs, key=lambda p: -p["score"])[:top]


def _selftest():
    w = weights()
    fake = {"id": 1, "name": "X", "title": "t", "type": "Speed", "rarity": 3, "char_id": 1, "hint_skills": [],
            "effects": [[1, 10, -1, -1, -1, -1, -1, 20, -1, -1, -1, 30], [19, 20, -1, -1, -1, -1, -1, -1, -1, -1, -1, 60]],
            "unique": {"level": 30, "effects": [{"type": 8, "value": 10}]}}
    e0, _ = effects_at(fake, 0)
    e4, _ = effects_at(fake, 4)
    assert e0 == {1: 20, 19: 43, 8: 10} and e4 == {1: 30, 19: 60, 8: 10}, (e0, e4)  # lv30 and lv50, linear between
    pri = w["priority"]["medium"]
    vals = [card_value(fake, lb, pri, w)["total"] for lb in range(5)]
    assert vals == sorted(vals) and vals[4] > vals[0], vals  # more LB never scores lower
    assert _cond_ok("distance_type==4&running_style==1", {"distance_cat": "long", "style": "front"})
    assert not _cond_ok("distance_type==4", {"distance_cat": "mile"})
    assert _cond_ok("distance_type==4@distance_type==2", {"distance_cat": "mile"})
    assert weights({"priority": {"long": {"speed": 0.5}}})["priority"]["long"] == {**WEIGHTS["priority"]["long"], "speed": 0.5}
    if gd.support_cards():  # needs the game DB cache
        tiers = support_tiers(w)
        assert tiers and all(r["scores"] == sorted(r["scores"]) for r in tiers)
        owned = {r["id"]: 4 for r in tiers[:10]}
        d = build_deck(owned, {"distance_cat": "medium", "distance_m": 2000}, None, w)
        assert len(d["deck"]) == 6 and sum(x["borrow"] for x in d["deck"]) <= 1
        assert len({gd.support_cards()[x["id"]]["char_id"] for x in d["deck"]}) == 6
    print("model selftest: PASS")


if __name__ == "__main__":
    _selftest()
