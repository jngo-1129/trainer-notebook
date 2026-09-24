"""QoL tools: dashboard, reset countdowns, LB planner, collection gaps, what-if pulls, pull luck,
run-log tuning, Champions Meeting prep, global search, recommendation-change alerts.

Safety: computations on the local profile + cached public data only. No game access, no network.
"""
from datetime import date, datetime, timedelta, timezone
from math import comb

import advisor
import banners
import gamedata as gd
import model
import racesim

# Global resets at 00:00 JST = 15:00 UTC (community-documented; editable in kv reset_utc_hour / weekly_reset_day)
RESET_UTC_HOUR = 15
WEEKLY_RESET_DAY = 6  # Sunday 15:00 UTC = Monday 00:00 JST
STAT_TYPES = ["Speed", "Stamina", "Power", "Guts", "Wit"]


def resets(kv: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    hour = int(kv.get("reset_utc_hour") or RESET_UTC_HOUR)
    wday = int(kv.get("weekly_reset_day") or WEEKLY_RESET_DAY)
    daily = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if daily <= now:
        daily += timedelta(days=1)
    weekly = daily + timedelta(days=(wday - daily.weekday()) % 7)
    return {"daily": daily.isoformat(), "weekly": weekly.isoformat(),
            "last_daily": (daily - timedelta(days=1)).isoformat(), "last_weekly": (weekly - timedelta(days=7)).isoformat()}


def checklist(items: list[dict], kv: dict, now: datetime | None = None) -> list[dict]:
    """items: [{text, freq: daily|weekly, done_at: iso}] -> with 'done' for the current reset period."""
    r = resets(kv, now)
    return [i | {"done": bool(i.get("done_at")) and i["done_at"] >= r["last_" + i.get("freq", "daily")]} for i in items]


def top_decks(owned: dict, w: dict) -> dict:
    return {d: model.build_deck(owned, {"distance_cat": d}, None, w, borrow=True) for d in model.DISTANCES}


def lb_planner(owned: dict[int, int], w: dict) -> list[dict]:
    """Which owned card to limit-break next: value gained by +1 LB, weighted up if it's in a best deck."""
    cards = gd.support_cards()
    pri = model.generic_priority(w)
    in_deck = {c["id"] for d in top_decks(owned, w).values() for c in d["deck"] if not c["borrow"]}
    out = []
    for cid, lb in owned.items():
        c = cards.get(cid)
        if not c or lb >= 4:
            continue
        gain = model.card_value(c, lb + 1, pri, w)["total"] - model.card_value(c, lb, pri, w)["total"]
        out.append({"id": cid, "label": f"{c['title']} {c['name']}", "lb": lb, "gain": round(gain * (1 if cid in in_deck else 0.3), 1),
                    "in_deck": cid in in_deck})
    return sorted(out, key=lambda r: -r["gain"])


def gaps(owned: dict[int, int], w: dict) -> list[dict]:
    """Per card type: your best vs the best on Global, ranked by how much you're missing."""
    cards = gd.support_cards()
    pri = model.generic_priority(w)
    out = []
    for t in STAT_TYPES:
        mine = [(model.card_value(cards[i], lb, pri, w)["total"], cards[i]) for i, lb in owned.items() if i in cards and cards[i]["type"] == t]
        best_gl = sorted(((model.card_value(c, 4, pri, w)["total"], c) for c in cards.values() if c["type"] == t and gd.on_global(c)),
                         key=lambda x: x[0])[-3:]
        if not best_gl:
            continue
        top = sum(v for v, _ in best_gl) / len(best_gl)
        have = max(mine, key=lambda x: x[0], default=(0, None))
        miss = max(0.0, top - have[0])
        out.append({"type": t, "best_owned": have[1] and f"{have[1]['title']} {have[1]['name']}", "best_value": round(have[0]),
                    "global_top": round(top), "missing": round(miss), "count": len(mine),
                    "suggest": [f"{c['title']} {c['name']}" for _, c in reversed(best_gl) if c["id"] not in owned][:2],
                    "message": f"You lack a good {t} card" if miss > top * 0.3 else ""})
    return sorted(out, key=lambda r: -r["missing"])


def whatif(kind: str, cid: int, lb: int, profile: dict, w: dict) -> dict:
    owned = {o["id"]: o["level"] for o in profile["owned"] if o["kind"] == kind}
    if kind == "support":
        before = top_decks(owned, w)
        after = top_decks(owned | {cid: max(lb, owned.get(cid, -1))}, w)
        rows = []
        for d in model.DISTANCES:
            b, a = {c["id"] for c in before[d]["deck"]}, {c["id"] for c in after[d]["deck"]}
            rows.append({"distance": d, "before": before[d]["score"], "after": after[d]["score"],
                         "delta": round(after[d]["score"] - before[d]["score"], 1),
                         "out": [gd.label("support", i) for i in b - a], "in": [gd.label("support", i) for i in a - b]})
        pri = model.generic_priority(w)
        tier_rows = model.support_tiers(w)
        tr = next((r for r in tier_rows if r["id"] == cid), None)
        return {"card": gd.label("support", cid), "lb": lb, "decks": rows, "tier": tr and tr["tiers"][lb],
                "value": model.card_value(gd.support_cards()[cid], lb, pri, w)["total"]}
    a = banners.advise_uma(cid, set(owned), {}, w)
    return {"card": gd.label("uma", cid), "gains": a.get("gains"), "verdict": a.get("verdict"), "why": a.get("why")}


def binom_cdf(k: int, n: int, p: float) -> float:
    return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))


def pull_luck(log: list[dict]) -> dict:
    """Actual vs expected SSRs / featured copies, and spark points per banner."""
    n = sum(r["pulls"] or 0 for r in log)
    ssr = sum(r["ssr"] or 0 for r in log)
    tgt = sum(r["target"] or 0 for r in log)
    exp_t = sum((r["pulls"] or 0) * (r["rate"] or 0.0075) for r in log)
    rate_t = exp_t / n if n else 0.0075
    by_banner = {}
    for r in log:
        by_banner[r["banner"] or "?"] = by_banner.get(r["banner"] or "?", 0) + (r["pulls"] or 0)
    return {"pulls": n, "ssr": ssr, "expected_ssr": round(n * 0.03, 1), "target": tgt, "expected_target": round(exp_t, 1),
            # share of players who'd do no better than you: >50% = luckier than average
            "ssr_luck": round(binom_cdf(ssr, n, 0.03) * 100) if n else None,
            "target_luck": round(binom_cdf(tgt, n, rate_t) * 100) if n else None,
            "spark": {b: {"points": p, "to_spark": max(0, 200 - p % 200), "sparks": p // 200} for b, p in by_banner.items()}}


def run_tuning(runs: list[dict], w: dict, min_runs: int = 3) -> dict:
    """Suggest priority tweaks from logged runs: stats that keep ending short of target get more weight."""
    out, override = [], {}
    for d in model.DISTANCES:
        rs = [r for r in runs if r.get("distance") == d and r.get("stats")]
        if len(rs) < min_runs:
            continue
        for s in model.STATS:
            ratio = sum(r["stats"].get(s, 0) for r in rs) / len(rs) / max(1, w["targets"][d][s])
            cur = w["priority"][d][s]
            if ratio < 0.85 or ratio > 1.1:
                new = round(min(1.3, cur + 0.1) if ratio < 0.85 else max(0.2, cur - 0.1), 2)
                out.append({"distance": d, "stat": s, "avg_vs_target": f"{ratio:.0%}", "priority": [cur, new]})
                override.setdefault("priority", {}).setdefault(d, {})[s] = new
    card_runs = {}
    for r in runs:
        for cid in r.get("deck") or []:
            card_runs.setdefault(cid, []).append(r.get("rating") or 0)
    avg = sum(r.get("rating") or 0 for r in runs) / len(runs) if runs else 0
    cards = sorted(({"label": gd.label("support", c), "runs": len(v), "avg_rating_delta": round(sum(v) / len(v) - avg)}
                    for c, v in card_runs.items() if len(v) >= 2), key=lambda x: -x["avg_rating_delta"])
    return {"suggestions": out, "override": override, "cards": cards[:10], "runs": len(runs)}


def cm_prep(cup: dict, profile: dict, w: dict) -> dict:
    """Everything for one Champions Meeting course: targets, your best umas, skills, deck."""
    dist, m = cup["distance_cat"], cup["distance_m"]
    track = next((i for i, n in gd.TRACKS.items() if n == cup.get("track")), None)
    umas = gd.uma_cards()
    owned_umas = [umas[o["id"]] for o in profile["owned"] if o["kind"] == "uma" and o["id"] in umas]
    ranked = sorted(({"id": u["id"], "label": gd.label("uma", u["id"]), **model.uma_value(u, dist, w)} for u in owned_umas),
                    key=lambda r: -r["score"])[:5]
    style = ranked[0]["style"] if ranked else "pace"
    target = {"distance_cat": dist, "distance_m": m, "style": style, "surface": cup.get("surface", "turf"),
              "direction": cup.get("direction")} | ({"track": track} if track else {})
    target = {k: v for k, v in target.items() if v}
    skills = sorted(((model.skill_value(i, target, w), i) for i, s in gd.skills().items()
                     if s.get("rarity") in (1, 2) and i < 1_000_000 and not s.get("unreleased")), reverse=True)[:15]
    owned_sup = {o["id"]: o["level"] for o in profile["owned"] if o["kind"] == "support"}
    deck = model.build_deck(owned_sup, target, umas[ranked[0]["id"]]["char_id"] if ranked else None, w)
    return {"cup": cup, "target": target, "stats": racesim.targets(m, style, w["targets"][dist]), "umas": ranked,
            "skills": [{"id": i, "name": gd.skill_names().get(i, i), "lengths": v} for v, i in skills if v > 0],
            "deck": deck["deck"], "deck_score": deck["score"]}


def search(q: str, limit: int = 20) -> list[dict]:
    q = q.lower().strip()
    if len(q) < 2:
        return []
    out = []
    for kind, cards in (("support", gd.support_cards()), ("uma", gd.uma_cards())):
        out += [{"kind": kind, "id": c["id"], "label": f"{c['title']} {c['name']}", "extra": c.get("type", "")}
                for c in cards.values() if q in f"{c['title']} {c['name']}".lower()]
    out += [{"kind": "skill", "id": i, "label": n, "extra": ""} for i, n in gd.skill_names().items() if q in n.lower()]
    out += [{"kind": "event", "id": e["name"], "label": e["name"], "extra": e["source"]} for e in advisor.find_events(q, 5)]
    return out[:limit]


def snapshot(owned: dict, w: dict) -> dict:
    """What the app currently recommends, to detect changes after a data update."""
    decks = top_decks(owned, w)
    return {"decks": {d: sorted(c["id"] for c in x["deck"]) for d, x in decks.items()},
            "top10": [r["id"] for r in model.support_tiers(w)[:10]]}


def diff_alerts(before: dict, after: dict) -> list[str]:
    out = []
    for d, ids in after.get("decks", {}).items():
        if before.get("decks", {}).get(d) not in (None, ids):
            added = set(ids) - set(before["decks"][d])
            out.append(f"Best {d} deck changed: now includes {', '.join(gd.label('support', i) for i in added) or 'a different mix'}")
    new_top = [i for i in after.get("top10", []) if i not in before.get("top10", [])]
    if before.get("top10") and new_top:
        out.append(f"New in the top 10 support cards: {', '.join(gd.label('support', i) for i in new_top)}")
    return out


def dashboard(profile: dict, w: dict) -> dict:
    kv = profile["kv"]
    owned = {o["id"]: o["level"] for o in profile["owned"] if o["kind"] == "support"}
    fc = banners.forecast()
    upcoming = [b for b in fc["banners"] if any(c["kind"] == "support" for c in b["cards"])][:2]
    best = None
    for b in upcoming:  # ponytail: only next 2 banners' support cards, advice runs several deck builds each
        for c in b["cards"]:
            if c["kind"] == "support":
                a = banners.advise(c["id"], owned, kv, w, horizon_days=0)
                if not best or a["expected_gain"] > best["expected_gain"]:
                    best = a | {"window": banners.window(b), "estimate": b["estimate"]}
    nxt = next((b for b in fc["banners"]), None)
    today = date.today().isoformat()
    cm = next((c for c in gd.curated().get("champions_meeting", []) if c.get("end", "") >= today), None)
    decks = top_decks(owned, w)
    return {
        "carats": int(float(kv.get("carats") or 0)),
        "projected": banners.income(kv, banners._d(nxt["from"])) if nxt else None,
        "next_banner": best, "gaps": [g for g in gaps(owned, w) if g["message"]][:3],
        "decks": {d: {"score": x["score"], "cards": [c["label"] + (" (borrow)" if c["borrow"] else "") for c in x["deck"]]} for d, x in decks.items()},
        "cm": cm, "resets": resets(kv), "owned_count": len(owned),
    }


def _selftest():
    now = datetime(2026, 9, 23, 16, 0, tzinfo=timezone.utc)  # a Wednesday, after today's reset
    r = resets({}, now)
    assert r["daily"].startswith("2026-09-24T15:00") and r["weekly"].startswith("2026-09-27T15:00"), r
    items = checklist([{"text": "a", "freq": "daily", "done_at": "2026-09-23T15:30:00+00:00"},
                       {"text": "b", "freq": "daily", "done_at": "2026-09-23T14:00:00+00:00"}], {}, now)
    assert items[0]["done"] and not items[1]["done"]
    luck = pull_luck([{"pulls": 100, "ssr": 3, "target": 1, "rate": 0.0075, "banner": "x"}])
    assert luck["expected_ssr"] == 3.0 and luck["spark"]["x"]["to_spark"] == 100
    runs = [{"distance": "long", "stats": {"speed": 1100, "stamina": 600, "power": 900, "guts": 500, "wit": 600}, "rating": 1}] * 3
    t = run_tuning(runs, model.weights())
    assert any(s["stat"] == "stamina" for s in t["suggestions"]) and t["override"]["priority"]["long"]["stamina"] > 1.0
    assert diff_alerts({"decks": {"long": [1, 2]}, "top10": [1]}, {"decks": {"long": [1, 3]}, "top10": [1]})
    print("tools selftest: PASS")


if __name__ == "__main__":
    _selftest()
