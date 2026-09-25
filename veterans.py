"""Veteran (trained uma) tools: cleanup advisor, parent pair planner, Team Trials lineup.

Safety: recommendations only. Nothing here deletes, locks or edits anything in the game; the user does that
by hand. Works on the local profile and cached public data.

APPROXIMATIONS: spark values are community rules of thumb (3-star blue/pink sparks are rare and valuable,
distance/track pinks fix weak aptitudes, meta skills matter). Weights live in VET and are editable
(weights override key "veterans").
"""
import gamedata as gd
import model

VET = {
    "stars": {1: 1, 2: 3, 3: 8},  # value by spark stars
    "blue": {"Speed": 1.0, "Stamina": 1.0, "Power": 0.9, "Guts": 0.4, "Wit": 0.6},
    "pink": {"Long": 1.0, "Medium": 0.9, "Mile": 0.9, "Sprint": 0.7, "Short": 0.7, "Dirt": 0.8, "Turf": 0.4,
             "Front Runner": 0.5, "Pace Chaser": 0.5, "Late Surger": 0.5, "End Closer": 0.5},
    "green_tier": {"S": 12, "A": 8, "B": 4}, "green_other": 2,
    "white_tier": {"S": 4, "A": 3, "B": 2}, "white_other": 0.6, "white_cap": 30,
    "rating_per_pt": 1 / 2000, "affinity": 0.1,
    "keep_pct": 80, "consider_pct": 40, "trash_below": 8,
}
TIERS = ["Keep (locked)", "Low priority", "Consider removing", "Trash"]


def _cfg(w: dict) -> dict:
    return VET | w.get("veterans", {})


def _skill_tiers() -> dict[str, str]:
    names = gd.skill_names()
    return {names.get(t["skill_id"], ""): t["tier"] for t in gd.curated().get("skill_tiers", [])}


def spark_score(s: dict, cfg: dict, tiers: dict[str, str]) -> float:
    base = cfg["stars"].get(s["stars"], 0)
    if s["color"] == "blue":
        return base * cfg["blue"].get(s["name"], 0.3) * 10
    if s["color"] == "pink":
        return base * cfg["pink"].get(s["name"], 0.3) * 10
    if s["color"] == "green":
        return s["stars"] * cfg["green_tier"].get(tiers.get(s["name"]), cfg["green_other"])
    return s["stars"] * cfg["white_tier"].get(tiers.get(s["name"]), cfg["white_other"])


def score(v: dict, cfg: dict, tiers: dict, next_chars: list[int]) -> tuple[float, str]:
    """(value, short description of what makes it valuable)."""
    parts = sorted(((spark_score(s, cfg, tiers), s) for s in v["sparks"]), key=lambda x: -x[0])
    white = min(cfg["white_cap"], sum(x for x, s in parts if s["color"] == "white"))
    sparks = sum(x for x, s in parts if s["color"] != "white") + white
    u = gd.uma_cards().get(v["card_id"])
    aff = max((gd.affinity(c, u["char_id"]) for c in next_chars if u and c != u["char_id"]), default=0)
    total = sparks + (v.get("rating") or 0) * cfg["rating_per_pt"] + aff * cfg["affinity"]
    best = ", ".join(f"{s['name']} {s['stars']}★" for x, s in parts[:3] if x >= 5)
    return round(total, 1), best


def protected_ids(vets: list[dict], plans: list[dict]) -> dict[int, str]:
    """Veterans that must never be suggested for deletion, with the reason."""
    out = {}
    for v in vets:
        for flag, why in (("locked", "locked in game"), ("pinned", "pinned by you"),
                          ("lineup", "in a Team Trials / Champions Meeting lineup"), ("rental", "rental / profile slot")):
            if v.get(flag):
                out.setdefault(v["id"], why)
    for p in plans:
        trainee = gd.label("uma", p.get("trainee_id", 0))
        for vid in p.get("parents", []):
            out.setdefault(vid, f"in your planned parent pair for {trainee}")
        for want in p.get("wants", []):  # sole source of a spark the plan needs
            holders = [v["id"] for v in vets if any(s["name"] == want["name"] for s in v["sparks"])]
            if len(holders) == 1:
                out.setdefault(holders[0], f"only source of {want['name']} for your {trainee} plan")
    return out


def cleanup(vets: list[dict], w: dict, plans: list[dict] = (), next_trainees: list[int] = (),
            free_slots: int = 0) -> dict:
    cfg = _cfg(w)
    tiers = _skill_tiers()
    umas = gd.uma_cards()
    next_chars = [umas[i]["char_id"] for i in next_trainees if i in umas]
    prot = protected_ids(vets, list(plans))
    rows = []
    for v in vets:
        val, best = score(v, cfg, tiers, next_chars)
        rows.append({"id": v["id"], "card_id": v["card_id"], "label": gd.label("uma", v["card_id"]), "score": val,
                     "best": best, "rating": v.get("rating"), "protected": prot.get(v["id"]),
                     "char": umas.get(v["card_id"], {}).get("char_id")})
    ordered = sorted(r["score"] for r in rows)
    pct = lambda p: ordered[min(len(ordered) - 1, int(len(ordered) * p / 100))] if ordered else 0
    keep_cut, consider_cut = pct(cfg["keep_pct"]), pct(cfg["consider_pct"])
    best_of_char = {}
    for r in rows:
        if r["char"] is not None and (r["char"] not in best_of_char or r["score"] > best_of_char[r["char"]]["score"]):
            best_of_char[r["char"]] = r
    for r in rows:
        top = best_of_char.get(r["char"])
        dup = top is not None and top is not r
        if r["protected"]:
            r["tier"], r["reason"] = TIERS[0], r["protected"]
        elif r["score"] >= keep_cut and r["score"] >= cfg["trash_below"] * 3:
            r["tier"], r["reason"] = TIERS[0], f"top parent material: {r['best'] or 'strong sparks'}"
        elif r["score"] < cfg["trash_below"]:
            r["tier"], r["reason"] = TIERS[3], f"no useful sparks{', low rating' if (r['rating'] or 0) < 10000 else ''}, not used anywhere"
        elif dup and r["score"] < top["score"]:
            r["tier"], r["reason"] = TIERS[2], f"duplicated by your better {top['label']} (#{top['id']})"
        elif r["score"] < consider_cut:
            r["tier"], r["reason"] = TIERS[2], f"weak sparks{': ' + r['best'] if r['best'] else ''}"
        else:
            r["tier"], r["reason"] = TIERS[1], f"decent ({r['best'] or 'some sparks'}), but you own better options"
    rows.sort(key=lambda r: (TIERS.index(r["tier"]), -r["score"]))
    removable = sorted((r for r in rows if not r["protected"]), key=lambda r: r["score"])
    suggest = removable[:free_slots] if free_slots else []
    note = (f"Only {len(removable)} veterans aren't protected; can't free {free_slots}." if free_slots > len(removable) else "")
    return {"rows": rows, "remove": [r["id"] for r in suggest], "note": note,
            "cutoffs": {"keep": keep_cut, "consider": consider_cut, "trash_below": cfg["trash_below"]}}


def plan_parents(vets: list[dict], trainee_id: int, wants: list[dict], w: dict, top: int = 5) -> list[dict]:
    """Pairs covering a target spark set. wants: [{name, stars}] = total stars wanted across both parents."""
    umas = gd.uma_cards()
    t = umas.get(trainee_id)
    if not t:
        return []
    cands = [v for v in vets if v["card_id"] in umas and umas[v["card_id"]]["char_id"] != t["char_id"]]
    target = {"distance_cat": "medium"}
    out = []
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            ca, cb = umas[a["card_id"]]["char_id"], umas[b["card_id"]]["char_id"]
            if ca == cb:
                continue
            have = {}
            for s in a["sparks"] + b["sparks"]:
                have[s["name"]] = have.get(s["name"], 0) + s["stars"]
            cover = [{"name": x["name"], "want": x["stars"], "have": have.get(x["name"], 0)} for x in wants]
            coverage = sum(min(1, c["have"] / max(1, c["want"])) for c in cover) / max(1, len(cover))
            aff = gd.affinity(t["char_id"], ca) + gd.affinity(t["char_id"], cb) + gd.affinity(t["char_id"], ca, cb)
            extra = sum(model.spark_value(s, t, target, w) for s in a["sparks"] + b["sparks"])
            out.append({"parents": [a["id"], b["id"]], "labels": [gd.label("uma", a["card_id"]), gd.label("uma", b["card_id"])],
                        "coverage": round(coverage, 2), "affinity": aff, "cover": cover,
                        "score": round(coverage * 100 + aff * 0.2 + extra * 0.05, 1)})
    return sorted(out, key=lambda p: -p["score"])[:top]


TT_SLOTS = {"short": "Sprint", "mile": "Mile", "medium": "Medium", "long": "Long", "dirt": "Dirt"}


def tt_lineup(vets: list[dict], per_slot: int = 3) -> dict:
    """Team Trials: 3 veterans per category, each used once, maximizing rating x aptitude.
    ponytail: greedy by best fit first; switch to an assignment solver if it picks visibly silly teams."""
    umas = gd.uma_cards()
    fits = []
    for v in vets:
        u = umas.get(v["card_id"])
        if not u:
            continue
        for cat in TT_SLOTS:
            if cat == "dirt":
                apt = model.APT_MULT.get(u["apt"]["dirt"], 0) * max(model.APT_MULT.get(u["apt"][d], 0) for d in ("short", "mile"))
            else:
                apt = model.APT_MULT.get(u["apt"][cat], 0) * model.APT_MULT.get(u["apt"]["turf"], 0)
            fits.append(((v.get("rating") or 0) * apt, cat, v))
    fits.sort(key=lambda x: -x[0])
    team = {c: [] for c in TT_SLOTS}
    used = set()
    for fit, cat, v in fits:
        if fit > 0 and v["id"] not in used and len(team[cat]) < per_slot:
            team[cat].append({"id": v["id"], "label": gd.label("uma", v["card_id"]), "fit": round(fit)})
            used.add(v["id"])
    return {"team": {TT_SLOTS[c]: m for c, m in team.items()}, "ids": sorted(used),
            "note": "Uses base aptitudes (sparks can raise them) and your rating entries. Assign in game yourself."}


def _selftest():
    w = model.weights()
    umas = [u for u in gd.uma_cards().values() if gd.on_global(u)][:8]
    if len(umas) < 8:
        print("veterans selftest: SKIP (no game data)")
        return
    vets = [{"id": i + 1, "card_id": u["id"], "rating": 12000, "sparks": []} for i, u in enumerate(umas)]
    vets[0]["sparks"] = [{"color": "blue", "name": "Stamina", "stars": 3}, {"color": "pink", "name": "Long", "stars": 3}]
    vets[1]["locked"] = 1
    vets[2]["sparks"] = [{"color": "pink", "name": "Dirt", "stars": 2}]
    plans = [{"trainee_id": umas[7]["id"], "parents": [], "wants": [{"name": "Dirt", "stars": 2}]}]
    r = cleanup(vets, w, plans, free_slots=20)
    by = {x["id"]: x for x in r["rows"]}
    assert by[1]["tier"] == TIERS[0] and "top parent" in by[1]["reason"], by[1]
    assert by[2]["tier"] == TIERS[0] and by[2]["protected"] == "locked in game"
    assert by[3]["protected"] and "only source" in by[3]["protected"]
    assert 2 not in r["remove"] and 3 not in r["remove"] and r["note"]  # never suggest protected; can't free 20
    assert by[4]["tier"] == TIERS[3]
    pp = plan_parents(vets, umas[7]["id"], [{"name": "Stamina", "stars": 3}], w)
    assert pp and pp[0]["coverage"] == 1.0 and 1 in pp[0]["parents"]
    tt = tt_lineup(vets)
    assert len(tt["ids"]) == len(set(tt["ids"])) <= 15
    print("veterans selftest: PASS")


if __name__ == "__main__":
    _selftest()
